import importlib.util
import json
from pathlib import Path

import pytest

from repolens.services.pr_report import MARKER, _owner_name
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_knowledge import make_collection, upload

ACTION = Path(__file__).resolve().parents[2] / "integrations" / "github-action" / "review.py"


def bearer(client, h, scope="read"):
    return {
        "Authorization": "Bearer "
        + client.post("/api/auth/api-tokens", headers=h, json={"name": "ci", "scope": scope}).json()["token"]
    }


@pytest.fixture
def indexed(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    col = make_collection(client, auth, "handbook")
    upload(
        client,
        auth,
        col["id"],
        {
            "deleted.md": "# Deleted users\n\nWhen `UserProfileService.get_profile` returns a default profile the name is Unknown.\n"
        },
    )
    return repo, col


FILES = [
    {"path": "src/user_profile.py", "additions": 12, "deletions": 3},
    {"path": "tests/test_user_profile.py", "additions": 20, "deletions": 0},
]


def report(client, h, **kw):
    body = {
        "repo": kw.pop("repo", "user-service"),
        "files": FILES,
        "title": "Fix deleted profile",
        "pr_number": 7,
        **kw,
    }
    return client.post("/api/risk/pr-report", headers=h, json=body)


def test_the_report_explains_the_risk_and_who_to_ask(client, auth, indexed):
    r = report(client, bearer(client, auth), repo=indexed[0]["id"], app_url="https://repolens.example.com/")
    assert r.status_code == 200, r.text
    d = r.json()
    md = d["markdown"]
    assert md.startswith(MARKER) and "### RepoLens review:" in md and f"({round(d['score'])}/100)" in md
    assert (
        "**Why**" in md
        and "**Who knows this code:**" in md
        and "Open in RepoLens" in md
        and "https://repolens.example.com/risk" in md
    )
    assert d["level"] in ("low", "medium", "high", "critical") and d["ref"] == "PR #7" and d["who"] and len(md) < 6000
    assert "tests/test_user_profile.py" in d["tests"]  # the test that covers the changed file


def test_nothing_is_stored_and_a_read_token_is_enough(client, auth, indexed, session):
    from repolens.models import RiskAssessment

    assert report(client, bearer(client, auth, "read"), repo=indexed[0]["name"]).status_code == 200
    assert session.query(RiskAssessment).count() == 0


def test_the_repository_can_be_named_by_its_github_owner_and_name(client, auth, indexed, session):
    from repolens.models import Repository

    repo = session.get(Repository, indexed[0]["id"])
    repo.url = "https://github.com/acme/user-service.git"
    session.commit()
    h = bearer(client, auth)
    assert report(client, h, repo="acme/user-service").status_code == 200
    assert report(client, h, repo="ACME/User-Service").status_code == 200
    assert report(client, h, repo="other/user-service").status_code == 404
    assert (
        _owner_name("git@github.com:acme/api.git") == "acme/api"
        and _owner_name("https://github.com/acme/api/") == "acme/api"
    )
    assert _owner_name("/srv/repos/api") == "repos/api" and _owner_name("api") == ""


def test_unknown_or_hidden_repositories_look_the_same_and_input_is_validated(client, auth, indexed):
    h = bearer(client, auth)
    assert report(client, h, repo="nope").status_code == 404
    assert client.post("/api/risk/pr-report", headers=h, json={"repo": "x", "files": []}).status_code == 422
    other = bearer(client, register(client, "z@other.io", "Z"))
    assert report(client, other, repo=indexed[0]["id"]).status_code == 404  # another workspace cannot see it
    assert client.post("/api/risk/pr-report", json={"repo": "x", "files": FILES}).status_code == 401


def test_other_repositories_are_left_out_of_a_comment_unless_asked(client, auth, indexed, session, monkeypatch):
    from repolens.services import pr_report as pr
    from repolens.services.access import UNRESTRICTED

    repo, col = indexed
    fake = {
        "tests_to_run": [],
        "docs_to_update": [
            {"title": "Private runbook", "repo_id": col["id"], "path": "r.md"},
            {"title": "Own doc", "repo_id": repo["id"], "path": "o.md"},
        ],
        "dependents": [{"name": "other_service_fn", "repo_id": col["id"]}],
        "files": [{"path": "src/user_profile.py"}],
    }
    from repolens.models import Repository
    from repolens.services import risk

    monkeypatch.setattr(
        risk,
        "assess",
        lambda db, ci, scope=None: {
            "score": 10,
            "level": "low",
            "signals": [],
            "affected": fake,
            "ref": "x",
            "title": "",
        },
    )
    r = session.get(Repository, repo["id"])
    summary = pr.build(session, r, UNRESTRICTED, FILES)["markdown"]
    full = pr.build(session, r, UNRESTRICTED, FILES, full=True)["markdown"]
    assert "Private runbook" not in summary and "other_service_fn" not in summary and "Own doc" in summary
    assert "Private runbook" in full and "other_service_fn" in full


# ------------------------------------------------------------------------------------------------ the Action's script
@pytest.fixture
def action():
    spec = importlib.util.spec_from_file_location("review", ACTION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_action(action, tmp_path, monkeypatch, *, existing=None, level="medium", env_extra=None, event=None):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps(event or {"pull_request": {"number": 7, "title": "Fix it"}}))
    calls = []

    def fake(method, url, token, body=None, accept="application/json"):
        calls.append((method, url, token, body))
        if "/pulls/7/files" in url:
            return [{"filename": "a.py", "additions": 3, "deletions": 1}]
        if url.endswith("/comments?per_page=100"):
            return existing or []
        if url.endswith("/api/risk/pr-report"):
            return {"markdown": f"{action.MARKER}\nhello", "level": level, "score": 55.0}
        return {}

    monkeypatch.setattr(action, "call", fake)
    env = {
        "GITHUB_EVENT_PATH": str(ev),
        "GITHUB_REPOSITORY": "acme/api",
        "REPOLENS_URL": "https://rl.example.com/",
        "REPOLENS_TOKEN": "rlp_x",
        "GITHUB_TOKEN": "gh_x",
        **(env_extra or {}),
    }
    return action.main(env), calls


def test_the_action_posts_one_comment_and_sends_only_file_names_and_counts(action, tmp_path, monkeypatch):
    code, calls = run_action(action, tmp_path, monkeypatch)
    assert code == 0
    sent = next(c for c in calls if c[1].endswith("/pr-report"))
    assert sent[3] == {
        "repo": "acme/api",
        "files": [{"path": "a.py", "additions": 3, "deletions": 1}],
        "title": "Fix it",
        "pr_number": 7,
        "app_url": "https://rl.example.com",
        "full": False,
    }
    assert sent[2] == "rlp_x"  # RepoLens gets its own token, GitHub gets GitHub's
    post = next(c for c in calls if c[0] == "POST" and "/issues/7/comments" in c[1])
    assert post[2] == "gh_x" and post[3]["body"].startswith(action.MARKER)


def test_the_action_updates_its_own_earlier_comment_instead_of_adding_another(action, tmp_path, monkeypatch):
    code, calls = run_action(
        action,
        tmp_path,
        monkeypatch,
        existing=[{"id": 1, "body": "someone else"}, {"id": 99, "body": f"{action.MARKER}\nold"}],
    )
    assert (
        code == 0
        and any(c[0] == "PATCH" and c[1].endswith("/issues/comments/99") for c in calls)
        and not any(c[0] == "POST" and "/comments" in c[1] for c in calls)
    )


def test_the_action_can_block_merges_at_a_risk_level(action, tmp_path, monkeypatch):
    assert run_action(action, tmp_path, monkeypatch, level="medium", env_extra={"REPOLENS_FAIL_ON": "high"})[0] == 0
    assert run_action(action, tmp_path, monkeypatch, level="high", env_extra={"REPOLENS_FAIL_ON": "high"})[0] == 1
    assert run_action(action, tmp_path, monkeypatch, level="critical", env_extra={"REPOLENS_FAIL_ON": "high"})[0] == 1
    assert run_action(action, tmp_path, monkeypatch, level="critical")[0] == 0  # no limit set


def test_a_missing_setup_or_a_fork_never_blocks_a_merge(action, tmp_path, monkeypatch, capsys):
    assert (
        run_action(action, tmp_path, monkeypatch, env_extra={"REPOLENS_TOKEN": ""})[0] == 0
        and "fork" in capsys.readouterr().out
    )
    assert run_action(action, tmp_path, monkeypatch, event={"push": {}})[0] == 0
    import urllib.error

    def refuse(method, url, token, body=None, accept="application/json"):
        if "/pulls/" in url:
            return [{"filename": "a.py"}]
        raise urllib.error.HTTPError(url, 404, "nf", {}, __import__("io").BytesIO(b'{"detail":"No repository"}'))

    monkeypatch.setattr(action, "call", refuse)
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"pull_request": {"number": 7}}))
    env = {
        "GITHUB_EVENT_PATH": str(ev),
        "GITHUB_REPOSITORY": "a/b",
        "REPOLENS_URL": "https://x",
        "REPOLENS_TOKEN": "t",
        "GITHUB_TOKEN": "g",
    }
    assert action.main(env) == 0
    assert action.main({**env, "REPOLENS_STRICT": "1"}) == 1
