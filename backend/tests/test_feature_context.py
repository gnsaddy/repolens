"""Build a feature with knowledge of other repositories: proven dependencies, and read-only code on request."""

import json

import pytest

from repolens.config import get_settings
from repolens.models import FeatureDraft
from repolens.services import features as svc
from repolens.services import llm
from repolens.services.llm import LLMResult
from tests.test_chat import make_user
from tests.test_groups import libs  # noqa: F401  (fixture: the shop and the library it imports)

LIB_REQUEST = "Add a tenant argument to get_user in the userlib core so lookups are scoped to a tenant"
SHOP_REQUEST = "In checkout make the get_user call pass a tenant and keep UserClient naming the user"

LIB_EDIT = {
    "spec": "Add a tenant to get_user.\n\n## Other repositories\nshop-app must pass the tenant.",
    "edits": [
        {
            "path": "userlib/core.py",
            "old": "def get_user(user_id):\n    return {'id': user_id}\n",
            "new": "def get_user(user_id, tenant):\n    return {'id': user_id, 'tenant': tenant}\n",
        }
    ],
    "new_files": [],
    "tests": [
        {
            "path": "tests/test_tenant.py",
            "content": "from userlib.core import get_user\n\n\ndef test_tenant():\n    assert get_user(1, 'a')['tenant'] == 'a'\n",
        }
    ],
    "pr": {"title": "Add a tenant to get_user", "body": "Callers must pass a tenant."},
}
SHOP_EDIT = {
    "spec": "Pass a tenant from checkout.",
    "edits": [
        {
            "path": "shop/checkout.py",
            "old": "    user = get_user(uid)\n",
            "new": "    user = get_user(uid, 'default')\n",
        }
    ],
    "new_files": [],
    "tests": [{"path": "tests/test_checkout.py", "content": "def test_checkout():\n    assert True\n"}],
    "pr": {"title": "Pass a tenant", "body": "x"},
}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    state = type("M", (), {"reply": LIB_EDIT, "calls": 0, "prompts": []})()

    def fake(system, prompt, max_tokens, cfg=None):
        state.calls += 1
        state.prompts.append(prompt)
        assert "untrusted" in system and "OTHER REPOSITORIES" in system
        return LLMResult(json.dumps(state.reply), 2000, 300)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    yield state
    get_settings.cache_clear()


def ask(client, h, repo, request, related=(), **extra):
    body = {"repo_id": repo["id"], "request": request, "related_repo_ids": [r["id"] for r in related], **extra}
    return client.post("/api/features", headers=h, json=body)


def test_the_model_is_told_who_depends_on_the_files_it_is_about_to_change(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    r = ask(client, auth, lib, LIB_REQUEST)
    assert r.status_code == 201, r.text
    prompt = model.prompts[0]
    assert "DEPENDENCIES (proven by the code):" in prompt and "OTHER REPOSITORIES" not in prompt
    assert "userlib/core.py is used by shop-app: shop/checkout.py" in prompt and "from userlib import" in prompt
    d = r.json()
    facts = d["context"]["facts"]
    assert facts and all(f["repo"] == "shop-app" and f["direction"] == "used_by" for f in facts)
    assert d["context"]["files"] == [] and d["related"] == []


def test_the_review_names_the_other_repositories_when_a_signature_changes(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    d = ask(client, auth, lib, LIB_REQUEST).json()
    msgs = [f["message"] for f in d["review"]["findings"]]
    assert any(
        "signature of get_user changed" in m and "Other repositories depend on this file: shop-app" in m for m in msgs
    )
    assert [i["file"] for i in d["impact"]] == ["userlib/core.py"] and d["impact"][0]["repos"] == ["shop-app"]


def test_chosen_repositories_show_their_code_read_only_and_the_draft_says_which(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    model.reply = SHOP_EDIT
    r = ask(client, auth, shop, SHOP_REQUEST, [lib])
    assert r.status_code == 201, r.text
    prompt = model.prompts[0]
    assert "OTHER REPOSITORIES (read-only context; never edit these files):" in prompt
    assert "FILE users-lib:userlib/core.py" in prompt and "def get_user(user_id):" in prompt
    d = r.json()
    assert d["related"] == [{"id": lib["id"], "name": "users-lib"}] and d["related_hidden"] == 0
    shown = [(f["repo"], f["path"]) for f in d["context"]["files"]]
    assert ("users-lib", "userlib/core.py") in shown and len(shown) <= svc.RELATED_FILES
    core = next(f for f in d["context"]["files"] if f["path"] == "userlib/core.py")
    assert core["why"].startswith("linked to shop/")
    assert any(f["direction"] == "uses" and f["repo"] == "users-lib" for f in d["context"]["facts"])
    assert "userlib/core.py" not in d["diff"]  # nothing in the other repository was changed


def test_an_edit_to_another_repository_is_refused_it_is_context_only(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    model.reply = LIB_EDIT  # names userlib/core.py while the draft is for the shop
    r = ask(client, auth, shop, SHOP_REQUEST, [lib])
    assert r.status_code == 409 and "could not be applied" in r.json()["detail"] and "not shown" in r.json()["detail"]


def test_a_repository_that_cannot_be_opened_or_is_the_same_one_is_not_found(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(f"/api/repositories/{lib['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []})
    assert ask(client, ann, shop, SHOP_REQUEST, [lib]).status_code == 404
    assert ask(client, auth, shop, SHOP_REQUEST, [shop]).status_code == 404
    assert ask(client, auth, shop, SHOP_REQUEST, [{"id": "nope"}]).status_code == 404
    assert model.calls == 0


def test_more_than_the_limit_of_repositories_is_refused(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    r = ask(client, auth, shop, SHOP_REQUEST, [lib] * 5)
    assert r.status_code == 422 and model.calls == 0


def test_a_draft_written_with_other_code_is_not_reused_for_the_same_words_without_it(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    model.reply = SHOP_EDIT
    with_lib = ask(client, auth, shop, SHOP_REQUEST, [lib]).json()
    alone = ask(client, auth, shop, SHOP_REQUEST).json()
    assert alone["id"] != with_lib["id"] and not alone["reused"] and model.calls == 2
    again = ask(client, auth, shop, SHOP_REQUEST, [lib]).json()
    assert again["id"] == with_lib["id"] and again["reused"] and model.calls == 2


def test_a_shared_draft_does_not_show_a_reader_code_or_names_from_a_repository_they_cannot_open(
    client, auth, libs, model, session
):  # noqa: F811
    shop, lib = libs
    model.reply = SHOP_EDIT
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    made = ask(client, ann, shop, SHOP_REQUEST, [lib]).json()
    url = f"/api/features/{made['id']}"
    client.patch(url, headers=ann, json={"shared": True})
    ann_id = client.get("/api/auth/me", headers=ann).json()["id"]
    client.put(
        f"/api/repositories/{lib['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": [ann_id]}
    )
    seen = client.get(url, headers=bob)
    assert seen.status_code == 200
    d = seen.json()
    assert d["related"] == [] and d["related_hidden"] == 1
    assert d["context"]["files"] == [] and d["context"]["facts"] == [] and d["context"]["hidden"] >= 1
    assert "users-lib" not in json.dumps({k: v for k, v in d.items() if k not in ("spec", "diff", "request")})
    mine = client.get(url, headers=ann).json()  # the author can still open it
    assert mine["related"] == [{"id": lib["id"], "name": "users-lib"}] and mine["context"]["files"]
    assert session.query(FeatureDraft).one().related_ids == [lib["id"]]


def test_facts_about_repositories_the_reader_cannot_open_are_counted_not_named(client, auth, libs, session):  # noqa: F811
    from repolens.models import Repository
    from repolens.services.access import Scope

    shop, lib = libs
    org = session.get(Repository, lib["id"]).org_id
    row = session.get(Repository, lib["id"])
    everyone = Scope(allowed=None)  # type: ignore[call-arg]
    seen, hidden = svc.dependency_facts(session, org, everyone, row, ["userlib/core.py"])
    assert seen and hidden == 0
    only_lib = Scope(allowed={lib["id"]})  # type: ignore[call-arg]
    none, hidden = svc.dependency_facts(session, org, only_lib, row, ["userlib/core.py"])
    assert none == [] and hidden >= 1
    assert "cannot see" in svc.facts_text(none, hidden) and "shop" not in svc.facts_text(none, hidden)
