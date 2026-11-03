"""Feature drafts: the model writes a change, RepoLens reads it, counts the tokens and applies nothing."""

import json

import pytest

from repolens.config import get_settings
from repolens.models import FeatureDraft
from repolens.services import features as svc
from repolens.services import llm
from repolens.services.llm import LLMResult
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_groups import days_ago
from tests.test_risk import commit, git

PRICING = (
    "def compute_total(items):\n    return sum(items)\n\n\ndef format_price(cents):\n    return f'{cents / 100:.2f}'\n"
)
CHECKOUT = "from pricing import compute_total\n\n\ndef checkout(items):\n    return compute_total(items)\n"
REQUEST = "Add a discount to compute_total in pricing so a percentage can be taken off the order total"

GOOD = {
    "spec": "Add an optional percentage discount to compute_total.\n- 0 means no discount\n- over 100 raises ValueError",
    "edits": [
        {
            "path": "app/pricing.py",
            "old": "def compute_total(items):\n    return sum(items)\n",
            "new": (
                "def compute_total(items, discount=0):\n    if not 0 <= discount <= 100:\n"
                "        raise ValueError('discount must be between 0 and 100')\n"
                "    return sum(items) * (100 - discount) // 100\n"
            ),
        }
    ],
    "new_files": [],
    "tests": [
        {
            "path": "tests/test_discount.py",
            "content": (
                "from app.pricing import compute_total\n\n\ndef test_discount():\n"
                "    assert compute_total([100, 100], 50) == 100\n"
            ),
        }
    ],
    "pr": {
        "title": "Add a discount to compute_total",
        "body": "Adds an optional percentage.\n\nNot done: rounding rules.",
    },
}


@pytest.fixture
def model(monkeypatch):
    """A fake hosted model; `model.reply` is what it answers, `model.calls` how often it was asked."""
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    state = type("M", (), {"reply": GOOD, "calls": 0, "fail": None})()

    def fake(system, prompt, max_tokens, cfg=None):
        state.calls += 1
        assert "untrusted" in system and REQUEST[:20] in prompt
        if state.fail:
            raise RuntimeError(state.fail)
        return LLMResult(json.dumps(state.reply), 2000, 300)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    yield state
    get_settings.cache_clear()


@pytest.fixture
def repo(client, auth, tmp_path):
    p = tmp_path / "shop"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    files = {"app/pricing.py": PRICING, "app/checkout.py": CHECKOUT, "README.md": "# shop\n"}
    commit(p, files, "feat: shop", days_ago(3))
    return add_repo(client, auth, p, name="shop")


def ask(client, h, repo, request=REQUEST):
    return client.post("/api/features", headers=h, json={"repo_id": repo["id"], "request": request})


def test_a_draft_comes_with_a_diff_a_review_the_impact_and_the_tokens_it_used(client, auth, repo, model):
    r = ask(client, auth, repo)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "drafted" and d["title"] == "Add a discount to compute_total" and model.calls == 1
    assert "+def compute_total(items, discount=0):" in d["diff"] and "tests/test_discount.py" in d["diff"]
    assert d["tokens"]["input_tokens"] == 2000 and d["tokens"]["output_tokens"] == 300 and d["tokens"]["calls"] == 1
    assert d["review"]["ok"] and "Read, not run" in d["review"]["note"]
    assert d["pr_body"].startswith("Adds an optional percentage") and "Nothing was applied" in d["note"]
    assert [i["file"] for i in d["impact"]] == ["app/pricing.py"]
    assert [f["path"] for f in d["new_files"]] == ["tests/test_discount.py"] and d["new_files"][0]["test"]


def test_a_changed_signature_names_the_callers_and_missing_tests_are_noticed(client, auth, repo, model):
    edit = {**GOOD["edits"][0], "new": "def compute_total(items, discount):\n    return sum(items)\n"}
    model.reply = {**GOOD, "tests": [], "edits": [edit]}
    d = ask(client, auth, repo).json()
    msgs = [f["message"] for f in d["review"]["findings"]]
    assert any("signature of compute_total changed" in m and "app/checkout.py" in m for m in msgs)
    assert "The draft adds no tests." in msgs


def test_reading_the_change_finds_unguarded_io_swallowed_errors_and_syntax_errors(client, auth, repo, model):
    code = (
        "import requests\n\n\ndef fetch(url):\n    return requests.get(url).text\n\n\n"
        "def quiet():\n    try:\n        return 1\n    except Exception:\n        pass\n"
    )
    files = [{"path": "app/net.py", "content": code}, {"path": "app/bad.py", "content": "def x(:\n"}]
    model.reply = {**GOOD, "tests": [], "new_files": files}
    d = ask(client, auth, repo).json()
    by = {(f["level"], f["file"]) for f in d["review"]["findings"]}
    text = " ".join(f["message"] for f in d["review"]["findings"])
    assert ("error", "app/bad.py") in by and not d["review"]["ok"]
    assert "does I/O (requests.get)" in text and "swallow errors" in text and "No new test mentions fetch" in text


def test_code_that_runs_commands_is_an_error(client, auth, repo, model):
    code = "import subprocess\n\n\ndef go():\n    subprocess.run(['ls'])\n"
    model.reply = {**GOOD, "new_files": [{"path": "app/run.py", "content": code}]}
    d = ask(client, auth, repo).json()
    assert not d["review"]["ok"] and any("runs commands" in f["message"] for f in d["review"]["findings"])


def test_an_edit_that_does_not_apply_fails_the_draft_and_the_tokens_are_counted(client, auth, repo, model, session):
    model.reply = {**GOOD, "edits": [{"path": "app/pricing.py", "old": "def nothing_like_it():", "new": "x"}]}
    r = ask(client, auth, repo)
    assert r.status_code == 409 and "could not be applied" in r.json()["detail"]
    row = session.query(FeatureDraft).one()
    assert row.status == "failed" and svc.ledger(session, row.org_id, row.id)["input_tokens"] == 2000


def test_paths_a_feature_may_not_use_are_refused(client, auth, repo, model):
    model.reply = {**GOOD, "new_files": [{"path": ".github/workflows/x.yml", "content": "on: push\n"}]}
    r = ask(client, auth, repo)
    assert r.status_code == 409 and ".github/workflows/x.yml" in r.json()["detail"]
    model.reply = {**GOOD, "new_files": [{"path": "app/pricing.py", "content": "x = 1\n"}]}
    assert "overwrite" in ask(client, auth, repo).json()["detail"]


def test_a_failed_model_call_is_reported_not_replaced_by_loose_text(client, auth, repo, model, session):
    model.fail = "overloaded"
    r = ask(client, auth, repo)
    assert r.status_code == 409 and "could not write the draft" in r.json()["detail"]
    assert session.query(FeatureDraft).one().status == "failed"


def test_without_a_model_it_says_so(client, auth, repo, monkeypatch):
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "heuristic")
    get_settings.cache_clear()
    r = ask(client, auth, repo)
    get_settings.cache_clear()
    assert r.status_code == 409 and "needs a language model" in r.json()["detail"]


def test_a_request_with_no_matching_code_is_refused_before_the_model_is_asked(client, auth, repo, model):
    r = ask(client, auth, repo, "Please build zebra giraffe telescope orchestration for the moon base")
    assert r.status_code == 409 and model.calls == 0


def test_drafts_are_private_to_their_author_and_admins_and_only_members_write(client, auth, repo, model):
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    assert ask(client, viewer, repo).status_code == 403
    mine = ask(client, ann, repo).json()
    assert client.get(f"/api/features/{mine['id']}", headers=bob).status_code == 404
    assert client.delete(f"/api/features/{mine['id']}", headers=bob).status_code == 404
    assert [d["id"] for d in client.get("/api/features", headers=ann).json()["drafts"]] == [mine["id"]]
    assert client.get("/api/features", headers=bob).json()["drafts"] == []
    assert len(client.get("/api/features", headers=auth).json()["drafts"]) == 1  # an admin sees every draft
    assert client.delete(f"/api/features/{mine['id']}", headers=ann).status_code == 204


def test_a_draft_for_a_repository_someone_cannot_open_is_not_found(client, auth, repo, model):
    ann = make_user(client, auth, "ann@acme.io", "member")
    body = {"visibility": "restricted", "user_ids": []}
    client.put(f"/api/repositories/{repo['id']}/access", headers=auth, json=body)
    assert ask(client, ann, repo).status_code == 404


def test_deleting_a_repository_removes_its_drafts(client, auth, repo, model, session):
    ask(client, auth, repo)
    assert client.delete(f"/api/repositories/{repo['id']}", headers=auth).status_code in (200, 204)
    session.expire_all()
    assert session.query(FeatureDraft).count() == 0


REWORDED = "Add a discount to compute_total in pricing, so a percentage can be taken off the order total."


def test_the_same_ask_in_other_words_reuses_the_earlier_draft_and_costs_nothing(client, auth, repo, model):
    first = ask(client, auth, repo).json()
    again = ask(client, auth, repo, REWORDED).json()
    assert model.calls == 1 and again["id"] == first["id"] and again["reused"] and not first["reused"]
    assert again["tokens"]["calls"] == 1  # the one call that made it


def test_asking_fresh_calls_the_model_again(client, auth, repo, model):
    first = ask(client, auth, repo).json()
    r = client.post("/api/features", headers=auth, json={"repo_id": repo["id"], "request": REWORDED, "fresh": True})
    assert model.calls == 2 and r.json()["id"] != first["id"] and not r.json()["reused"]


def test_a_different_ask_or_another_person_does_not_reuse_a_draft(client, auth, repo, model):
    ask(client, auth, repo)
    other = "Add a discount to compute_total in pricing for gold customers only, with an audit entry for each use"
    assert not ask(client, auth, repo, other).json()["reused"]
    ann = make_user(client, auth, "ann@acme.io", "member")
    assert not ask(client, ann, repo).json()["reused"] and model.calls == 3


def test_a_changed_index_does_not_reuse_a_draft(client, auth, repo, model, session):
    from repolens.models import Repository

    made = ask(client, auth, repo).json()
    row = session.get(Repository, repo["id"])
    user = session.get(FeatureDraft, made["id"]).created_by
    assert svc.earlier(session, row.org_id, user, row, REWORDED).id == made["id"]
    row.indexed_sha = "f" * 40  # the repository was indexed again: the code the draft was written against is gone
    assert svc.earlier(session, row.org_id, user, row, REWORDED) is None


def test_a_long_file_is_shown_only_around_the_match_and_a_short_one_whole():
    text = "\n".join(f"line_{n} = {n}" for n in range(1, 401))
    shown = svc._shown(text, [(200, 210)])
    assert "line_205 = 205" in shown and "line_5 = 5" not in shown and "only the lines around the match" in shown
    assert len(shown) < len(text) / 2
    small = "\n".join(f"x{n} = {n}" for n in range(1, 40))
    assert "x39 = 39" in svc._shown(small, [(1, 1)]) and "only the lines" not in svc._shown(small, [(1, 1)])
