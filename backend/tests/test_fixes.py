import base64

import httpx
import pytest

from repolens.api import fixes as fixes_api
from repolens.api import github as gh_api
from repolens.models import Investigation
from repolens.services import fixes
from tests.conftest import register
from tests.test_api import add_repo, run_investigation
from tests.test_github import TOKEN, connect

FILE = 'def get(d):\n    return d["name"]\n'
OLD = 'user["metadata"]["display_name"]'
NEW = 'user["metadata"].get("display_name", "Unknown")'


def draft(path="src/user_profile.py", old=OLD, new=NEW):
    return fixes.Draft(
        "Guard the lookup",
        "A deleted user has no metadata.",
        [{"path": path, "old": old, "new": new}],
        ["test deleted user"],
        "llm",
        "m",
    )


# ---------- the rules an edit must obey ----------
def test_edits_apply_only_when_the_old_text_appears_exactly_once():
    out = fixes.apply_edits({"a.py": FILE}, [{"path": "a.py", "old": 'd["name"]', "new": 'd.get("name")'}])
    assert out["a.py"] == 'def get(d):\n    return d.get("name")\n'
    for edit, why in (
        ({"path": "a.py", "old": "nope", "new": "x"}, "0 times"),
        ({"path": "a.py", "old": "d", "new": "x"}, "times"),
        ({"path": "b.py", "old": "x", "new": "y"}, "not part of the evidence"),
        ({"path": "a.py", "old": "def", "new": "def"}, "changes nothing"),
    ):
        with pytest.raises(fixes.FixError, match=why):
            fixes.apply_edits({"a.py": FILE}, [edit])


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "Dockerfile",
        "deploy/k8s/app.yaml",
        ".env.prod",
        "id.pem",
        "package.json",
        "poetry.lock",
        "requirements.txt",
        "../x.py",
        "/etc/passwd",
    ],
)
def test_build_deploy_dependency_and_secret_files_are_never_edited(path):
    assert fixes.forbidden(path)
    with pytest.raises(fixes.FixError):
        fixes.apply_edits({path: "a b"}, [{"path": path, "old": "a", "new": "c"}])


def test_ordinary_source_is_allowed_and_checks_catch_syntax_errors_and_big_changes():
    assert not fixes.forbidden("src/app/service.py")
    before = {"a.py": FILE}
    bad = fixes.apply_edits(before, [{"path": "a.py", "old": 'd["name"]', "new": "d["}])
    assert fixes.check(before, bad)["syntax_ok"] is False
    huge = fixes.apply_edits(
        before,
        [{"path": "a.py", "old": "return", "new": "\n    ".join(f"x{i} = {i}" for i in range(300)) + "\n    return"}],
    )
    c = fixes.check(before, huge)
    assert c["syntax_ok"] is False and "limited" in c["notes"][0]


def test_the_diff_masks_secrets():
    d = fixes.make_diff({"a.py": "k = 1\n"}, {"a.py": 'k = "ghp_' + "a" * 36 + '"\n'})
    assert "ghp_" + "a" * 36 not in d and d.startswith("--- a/a.py")


# ---------- end to end ----------
@pytest.fixture
def pr_flow(monkeypatch, demo_repo):
    state = {"puts": [], "pulls": [], "refs": [], "fail_put": False}
    path = "src/user_profile.py"
    text = (demo_repo[0] / path).read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        if p == "/repos/acme/user-service" and m == "GET":
            if state.get("repo_status"):
                return httpx.Response(state["repo_status"])
            return httpx.Response(200, json={"default_branch": "main", **state.get("repo_info", {})})
        if p == "/repos/acme/user-service/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "b" * 40}})
        if p == "/repos/acme/user-service/git/refs" and m == "POST":
            state["refs"].append(request.read())
            return httpx.Response(201, json={})
        if p == f"/repos/acme/user-service/contents/{path}" and m == "GET":
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(state.get("text", text).encode()).decode(),
                    "sha": "blob1",
                },
            )
        if p == f"/repos/acme/user-service/contents/{path}" and m == "PUT":
            state["puts"].append(request.read())
            return httpx.Response(200, json={})
        if p == "/repos/acme/user-service/pulls" and m == "POST":
            state["pulls"].append(request.read())
            return httpx.Response(201, json={"html_url": "https://github.com/acme/user-service/pull/9"})
        if p == "/user":
            return httpx.Response(200, json={"login": "bot"})
        return httpx.Response(404)

    monkeypatch.setattr(gh_api, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(fixes_api, "resolve_gh_repo", lambda repo, explicit: "acme/user-service")
    state["text"] = text
    return state


@pytest.fixture
def confirmed(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    return inv["id"]


def confirm(client, auth, inv_id):
    assert (
        client.post(f"/api/investigations/{inv_id}/review", headers=auth, json={"outcome": "confirmed"}).status_code
        == 200
    )


def test_a_fix_is_only_drafted_from_a_confirmed_root_cause(client, auth, confirmed):
    r = client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth)
    assert r.status_code == 422 and "Confirm the root cause" in r.json()["detail"]


def test_without_a_model_a_cause_with_no_template_gets_an_honest_refusal(client, auth, confirmed):
    confirm(client, auth, confirmed)
    r = client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth)
    assert r.status_code == 422 and "No safe fix" in r.json()["detail"]


def test_propose_review_download_and_open_a_draft_pull_request(client, auth, confirmed, monkeypatch, pr_flow):
    confirm(client, auth, confirmed)
    monkeypatch.setattr(fixes, "_ask_model", lambda *a, **k: draft())
    connect(client, auth, TOKEN)
    r = client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["status"] == "pending" and f["files"] == ["src/user_profile.py"] and f["checks"]["syntax_ok"]
    assert '-        return {"id": user_id, "display_name": user["metadata"]["display_name"]}' in f["diff"]
    assert client.get(f"/api/investigations/{confirmed}/fix-proposals", headers=auth).json()[0]["id"] == f["id"]
    patch = client.get(f"/api/fix-proposals/{f['id']}/patch", headers=auth)
    assert patch.headers["content-disposition"].startswith("attachment") and patch.text == f["diff"]
    assert not pr_flow["pulls"]  # drafting never touches GitHub

    done = client.post(f"/api/fix-proposals/{f['id']}/decision", headers=auth, json={"decision": "approve"})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "approved" and body["pr_url"].endswith("/pull/9")
    assert b'"draft":true' in pr_flow["pulls"][0].replace(b" ", b"") and b"repolens/fix-" in pr_flow["refs"][0]
    sent = base64.b64decode(__import__("json").loads(pr_flow["puts"][0])["content"]).decode()
    assert NEW in sent and OLD not in sent
    again = client.post(f"/api/fix-proposals/{f['id']}/decision", headers=auth, json={"decision": "reject"})
    assert again.status_code == 409  # decided once


def _pending(client, auth, confirmed, monkeypatch):
    confirm(client, auth, confirmed)
    monkeypatch.setattr(fixes, "_ask_model", lambda *a, **k: draft())
    connect(client, auth, TOKEN)
    return client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth).json()


def test_a_public_target_needs_an_explicit_confirmation_before_anything_is_published(
    client, auth, confirmed, monkeypatch, pr_flow
):
    pr_flow["repo_info"] = {"private": False, "visibility": "public"}
    f = _pending(client, auth, confirmed, monkeypatch)
    assert f["target_public"] is True
    assert client.get(f"/api/fix-proposals/{f['id']}", headers=auth).json()["target_public"] is True
    url = f"/api/fix-proposals/{f['id']}/decision"
    for body in ({"decision": "approve"}, {"decision": "approve", "confirm_public": False}):
        r = client.post(url, headers=auth, json=body)
        assert r.status_code == 409 and "public repository" in r.json()["detail"]
    assert not pr_flow["refs"] and not pr_flow["puts"] and not pr_flow["pulls"]
    assert client.get(f"/api/fix-proposals/{f['id']}", headers=auth).json()["status"] == "pending"  # still decidable
    ok = client.post(url, headers=auth, json={"decision": "approve", "confirm_public": True})
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and pr_flow["pulls"]


def test_rejecting_a_fix_for_a_public_repository_needs_no_confirmation(client, auth, confirmed, monkeypatch, pr_flow):
    pr_flow["repo_info"] = {"private": False}
    f = _pending(client, auth, confirmed, monkeypatch)
    r = client.post(f"/api/fix-proposals/{f['id']}/decision", headers=auth, json={"decision": "reject"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"


@pytest.mark.parametrize("info", [{"private": True, "visibility": "private"}, {"visibility": "internal"}])
def test_a_private_target_behaves_as_before(client, auth, confirmed, monkeypatch, pr_flow, info):
    pr_flow["repo_info"] = info
    f = _pending(client, auth, confirmed, monkeypatch)
    assert f["target_public"] is False
    r = client.post(f"/api/fix-proposals/{f['id']}/decision", headers=auth, json={"decision": "approve"})
    assert r.status_code == 200 and pr_flow["pulls"]


def test_when_github_cannot_say_the_visibility_is_unknown_and_reading_still_works(
    client, auth, confirmed, monkeypatch, pr_flow
):
    f = _pending(client, auth, confirmed, monkeypatch)
    assert f["target_public"] is None  # GitHub answered without visibility
    pr_flow["repo_status"] = 500
    got = client.get(f"/api/fix-proposals/{f['id']}", headers=auth)
    assert got.status_code == 200 and got.json()["target_public"] is None


def test_a_fix_is_refused_when_the_code_moved_on(client, auth, confirmed, monkeypatch, pr_flow):
    confirm(client, auth, confirmed)
    monkeypatch.setattr(fixes, "_ask_model", lambda *a, **k: draft())
    connect(client, auth, TOKEN)
    f = client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth).json()
    pr_flow["text"] = pr_flow["text"].replace(OLD, "None")
    r = client.post(f"/api/fix-proposals/{f['id']}/decision", headers=auth, json={"decision": "approve"})
    assert r.status_code == 409 and "changed since" in r.json()["detail"]
    assert not pr_flow["puts"] and not pr_flow["pulls"]
    assert client.get(f"/api/fix-proposals/{f['id']}", headers=auth).json()["status"] == "failed"


def test_reject_changes_nothing_and_a_model_draft_that_does_not_apply_is_dropped(
    client, auth, confirmed, monkeypatch, pr_flow
):
    confirm(client, auth, confirmed)
    monkeypatch.setattr(fixes, "_ask_model", lambda *a, **k: draft(old="not in the file"))
    assert client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth).status_code == 422
    monkeypatch.setattr(fixes, "_ask_model", lambda *a, **k: draft(path=".github/workflows/ci.yml"))
    assert client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth).status_code == 422
    monkeypatch.setattr(fixes, "_ask_model", lambda *a, **k: draft())
    f = client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth).json()
    r = client.post(f"/api/fix-proposals/{f['id']}/decision", headers=auth, json={"decision": "reject"})
    assert r.json()["status"] == "rejected" and not pr_flow["pulls"]


def test_viewers_cannot_propose_or_decide_and_other_workspaces_cannot_see(client, auth, confirmed, monkeypatch):
    confirm(client, auth, confirmed)
    monkeypatch.setattr(fixes, "_ask_model", lambda *a, **k: draft())
    f = client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=auth).json()
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-12", "role": "viewer"}
    )
    v = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-12"}).json()[
            "access_token"
        ]
    }
    assert client.post(f"/api/investigations/{confirmed}/fix-proposals", headers=v).status_code == 403
    assert (
        client.post(f"/api/fix-proposals/{f['id']}/decision", headers=v, json={"decision": "reject"}).status_code == 403
    )
    other = register(client, "z@other.io", "Other")
    assert client.get(f"/api/fix-proposals/{f['id']}", headers=other).status_code == 404
    assert client.get(f"/api/fix-proposals/{f['id']}/patch", headers=other).status_code == 404


def test_the_plain_keyerror_template_guards_the_read(session):
    ev = [
        {
            "type": "code",
            "file": "src/f.py",
            "start_line": 8,
            "snippet": '    return [{"name": m["name"]} for m in members]',
        }
    ]
    inv = Investigation(org_id="o", title="t", query="Traceback\nKeyError: 'name'")
    text = "x = 1\n" * 7 + '    return [{"name": m["name"]} for m in members]\n'
    inv.hypotheses = []
    from repolens.models import Hypothesis

    inv.hypotheses.append(Hypothesis(title="h", evidence=ev))
    d = fixes._deterministic(inv, {"src/f.py": text})
    assert d and d.by == "template" and '.get("name")' in d.edits[0]["new"] and "decide whether" in d.rationale
    assert fixes.apply_edits({"src/f.py": text}, d.edits)["src/f.py"].count(".get(") == 1
