import hashlib
import hmac
import json

import httpx
import pytest

from repolens.api import jira as jira_api
from repolens.demo import BUG_REPORT
from tests.conftest import register


def add_repo(client, auth, path, name="user-service"):
    r = client.post("/api/repositories", headers=auth, json={"name": name, "source": "local", "url": str(path)})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- auth ----------
def test_auth_flow(client):
    h = register(client)
    assert client.get("/api/auth/me", headers=h).json()["role"] == "admin"
    assert (
        client.post("/api/auth/login", json={"email": "admin@acme.io", "password": "correct-horse-1"}).status_code
        == 200
    )
    assert client.post("/api/auth/login", json={"email": "admin@acme.io", "password": "wrong"}).status_code == 401
    assert client.get("/api/repositories").status_code == 401
    assert client.get("/api/repositories", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_duplicate_registration_and_weak_password(client):
    register(client)
    r = client.post("/api/auth/register", json={"email": "admin@acme.io", "password": "correct-horse-1"})
    assert r.status_code == 409
    assert client.post("/api/auth/register", json={"email": "b@b.io", "password": "short"}).status_code == 422


def test_rbac_viewer_cannot_create_repo(client, auth, demo_repo):
    client.post(
        "/api/auth/users",
        headers=auth,
        json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"},
    )
    tok = client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()[
        "access_token"
    ]
    r = client.post(
        "/api/repositories",
        headers={"Authorization": f"Bearer {tok}"},
        json={"name": "x", "source": "local", "url": str(demo_repo[0])},
    )
    assert r.status_code == 403


# ---------- repositories / search ----------
def test_index_search_and_file_view(client, auth, demo_repo):
    path, _ = demo_repo
    repo = add_repo(client, auth, path)
    detail = client.get(f"/api/repositories/{repo['id']}", headers=auth).json()
    assert detail["status"] == "ready" and detail["chunk_count"] > 0 and detail["indexed_sha"]
    hits = client.get("/api/search", headers=auth, params={"q": "get profile metadata display name"}).json()
    assert hits and hits[0]["file"] == "src/user_profile.py"
    commits = client.get(f"/api/repositories/{repo['id']}/commits", headers=auth).json()
    assert len(commits) == 3
    f = client.get(f"/api/repositories/{repo['id']}/file", headers=auth, params={"path": "src/user_profile.py"}).json()
    assert "get_profile" in f["content"] and any(s["name"] == "get_profile" for s in f["symbols"])


def test_file_endpoint_rejects_unindexed_paths(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    for bad in ("../../etc/passwd", "/etc/passwd", ".git/config"):
        assert client.get(f"/api/repositories/{repo['id']}/file", headers=auth, params={"path": bad}).status_code == 404


def test_incremental_reindex_reuses_chunks(client, auth, demo_repo, session):
    path, _ = demo_repo
    repo = add_repo(client, auth, path)
    (path / "src/extra.py").write_text("def extra():\n    return 1\n")
    import subprocess

    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add extra"],
        check=True,
    )
    assert client.post(f"/api/repositories/{repo['id']}/index", headers=auth).status_code == 202
    runs = client.get(f"/api/repositories/{repo['id']}/runs", headers=auth).json()
    assert runs[0]["status"] == "completed"
    assert runs[0]["chunks_written"] == 1 and runs[0]["chunks_reused"] > 0 and runs[0]["commits"] == 1


def test_non_git_path_rejected(client, auth, tmp_path):
    r = client.post("/api/repositories", headers=auth, json={"name": "x", "source": "local", "url": str(tmp_path)})
    assert r.status_code == 400


def test_tenant_isolation(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    other = register(client, "evil@other.io", "Other")
    assert client.get(f"/api/repositories/{repo['id']}", headers=other).status_code == 404
    assert client.get("/api/repositories", headers=other).json() == []
    assert client.get("/api/search", headers=other, params={"q": "profile"}).json() == []
    r = client.post("/api/investigations", headers=other, json={"query": "profile broken", "repo_ids": [repo["id"]]})
    assert r.status_code == 404
    inv = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [repo["id"]]}).json()
    assert client.get(f"/api/investigations/{inv['id']}", headers=other).status_code == 404


# ---------- investigation (end to end) ----------
def run_investigation(client, auth, repo_id):
    r = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [repo_id]})
    assert r.status_code == 202
    return client.get(f"/api/investigations/{r.json()['id']}", headers=auth).json()


def test_investigation_finds_regression_commit(client, auth, demo_repo):
    path, bad_sha = demo_repo
    repo = add_repo(client, auth, path)
    inv = run_investigation(client, auth, repo["id"])
    assert inv["status"] == "completed", inv.get("error")
    top = inv["hypotheses"][0]
    assert "user_profile.py" in top["title"] and bad_sha[:8] in top["title"]
    assert top["status"] == "supported" and 0.5 < top["confidence"] <= 0.92
    kinds = {e["type"] for e in top["evidence"]}
    assert {"code", "commit"} <= kinds
    assert inv["verification"]["valid"] is True
    assert inv["root_cause_status"] == "unconfirmed"
    assert "Not yet confirmed" in inv["report_md"] and bad_sha[:10] in inv["report_md"]
    assert [s["step"] for s in inv["timeline"]][:3] == ["planner", "gather", "rank"]
    assert "No hosted model was called" in inv["report_md"]  # offline: never fabricates token usage
    assert "### Who to ask" in inv["report_md"] and "a hint, not a verdict" in inv["report_md"]


def test_investigation_with_no_relevant_code(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    r = client.post(
        "/api/investigations",
        headers=auth,
        json={"query": "zzqx kwyjibo flarbnitz", "repo_ids": [repo["id"]]},
    )
    inv = client.get(f"/api/investigations/{r.json()['id']}", headers=auth).json()
    assert inv["status"] == "completed" and inv["hypotheses"] == [] and inv["confidence"] == 0


def test_review_confirms_and_feeds_memory(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    r = client.post(f"/api/investigations/{inv['id']}/review", headers=auth, json={"outcome": "confirmed"})
    assert r.json()["root_cause_status"] == "confirmed"
    # the next, similar investigation recalls the earlier incident
    inv2 = run_investigation(client, auth, repo["id"])
    assert inv2["evidence"]["memory"] and inv2["evidence"]["memory"][0]["verified"] is True
    dash = client.get("/api/dashboard", headers=auth).json()
    assert dash["verified_accuracy"] == 1.0 and dash["completed"] == 2


def test_sse_events(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    with client.stream("GET", f"/api/investigations/{inv['id']}/events", headers=auth) as r:
        body = "".join(r.iter_text())
    assert "event: step" in body and "event: done" in body


class FakeLLM:
    calls = 0


def test_llm_path_records_usage_and_caches(client, auth, demo_repo, monkeypatch, session):
    from repolens.config import get_settings
    from repolens.services import llm
    from repolens.services.llm import LLMResult

    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("REPOLENS_PRICING_JSON", '{"claude-sonnet-5-5": {"input": 3.0, "output": 15.0}}')
    get_settings.cache_clear()

    def fake(system, prompt, max_tokens, cfg=None):
        FakeLLM.calls += 1
        assert "untrusted" in system
        return LLMResult(
            json.dumps(
                {
                    "root_cause": "Null guard removed [E1]",
                    "explanation": "e",
                    "suggested_fix": "restore guard",
                }
            ),
            2000,
            300,
        )

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    repo = add_repo(client, auth, demo_repo[0])
    inv1 = run_investigation(client, auth, repo["id"])
    inv2 = run_investigation(client, auth, repo["id"])
    assert FakeLLM.calls == 1 and inv1["root_cause"].startswith("Null guard") and inv2["cache_hit"] is True
    s = client.get("/api/usage/summary", headers=auth).json()
    assert s["totals"]["cache_hits"] == 1 and s["totals"]["tokens_avoided"] == 2300
    chat = [
        e for e in client.get("/api/usage/events", headers=auth).json() if e["kind"] == "chat" and not e["cache_hit"]
    ]
    assert chat[0]["input_tokens"] == 2000 and chat[0]["estimated_cost"] == pytest.approx(0.0105)
    assert "$0.0105" in inv1["report_md"]


def test_llm_budget_falls_back_to_deterministic(client, auth, demo_repo, monkeypatch):
    from repolens.config import get_settings
    from repolens.services import llm, usage

    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("REPOLENS_DAILY_BUDGET_USD", "0.01")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "_anthropic_call", lambda *a: pytest.fail("must not call the model over budget"))
    monkeypatch.setattr(usage, "spend_since", lambda *a, **k: 5.0)
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    assert inv["status"] == "completed" and inv["evidence"]["llm_used"] is False


def test_llm_provider_failure_does_not_fail_investigation(client, auth, demo_repo, monkeypatch):
    from repolens.config import get_settings
    from repolens.services import llm

    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()

    def boom(*a):
        raise RuntimeError("503 upstream")

    monkeypatch.setattr(llm, "_anthropic_call", boom)
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    assert inv["status"] == "completed"
    ev = client.get("/api/usage/events", headers=auth).json()
    assert any(not e["success"] for e in ev)


# ---------- Jira ----------
def jira_transport(state):
    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        p = request.url.path
        if p.endswith("/myself"):
            return httpx.Response(200, json={"displayName": "Bot"})
        if "/issue/BUG-1/comment" in p:
            if state.get("fail_comment"):
                return httpx.Response(500)
            state["comment"] = json.loads(request.content)
            return httpx.Response(201, json={"id": "10001"})
        if p.endswith("/issue/BUG-1"):
            return httpx.Response(
                200,
                json={
                    "key": "BUG-1",
                    "fields": {
                        "summary": "Profile 500 for deleted users",
                        "status": {"name": "Open"},
                        "issuetype": {"name": "Bug"},
                        "labels": [],
                        "components": [{"name": "user-service"}],
                        "description": {
                            "type": "doc",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": BUG_REPORT}]}],
                        },
                        "comment": {"comments": []},
                    },
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def jira_state(monkeypatch):
    state = {"requests": []}
    monkeypatch.setattr(jira_api, "_transport", jira_transport(state))
    return state


def configure_jira(client, auth, allow_publish=True):
    r = client.put(
        "/api/jira/connection",
        headers=auth,
        json={
            "base_url": "https://acme.atlassian.net",
            "deployment": "cloud",
            "email": "bot@acme.io",
            "token": "super-secret-token",
            "allow_publish": allow_publish,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_jira_secret_never_returned_and_encrypted(client, auth, session):
    out = configure_jira(client, auth)
    assert "token" not in out and "secret" not in json.dumps(out)
    assert "super-secret-token" not in json.dumps(client.get("/api/jira/connection", headers=auth).json())
    from repolens.models import JiraConnection

    assert "super-secret-token" not in session.query(JiraConnection).one().secret_encrypted


def test_jira_cloud_requires_email(client, auth):
    r = client.put("/api/jira/connection", headers=auth, json={"base_url": "https://a.atlassian.net", "token": "abcd"})
    assert r.status_code == 400


def test_jira_investigate_and_publish_with_approval(client, auth, demo_repo, jira_state):
    repo = add_repo(client, auth, demo_repo[0])
    configure_jira(client, auth)
    assert client.post("/api/jira/test", headers=auth).json() == {"ok": True, "user": "Bot"}
    r = client.post("/api/jira/investigate", headers=auth, json={"issue_key": "BUG-1", "repo_ids": [repo["id"]]})
    assert r.status_code == 202
    inv_id = r.json()["investigation_id"]
    inv = client.get(f"/api/investigations/{inv_id}", headers=auth).json()
    assert inv["status"] == "completed" and inv["jira_issue_key"] == "BUG-1" and "BUG-1" in inv["report_md"]
    # no approval -> denied, nothing sent
    r = client.post("/api/jira/publish", headers=auth, json={"investigation_id": inv_id})
    assert r.status_code == 403 and "comment" not in jira_state
    r = client.post("/api/jira/publish", headers=auth, json={"investigation_id": inv_id, "approved": True})
    assert r.status_code == 200 and r.json()["comment_id"] == "10001"
    assert jira_state["comment"]["body"]["type"] == "doc"  # ADF for Jira Cloud
    events = client.get("/api/jira/publish-events", headers=auth).json()
    assert events[0]["status"] == "published"
    auth_header = jira_state["requests"][0].headers["authorization"]
    assert auth_header.startswith("Basic ")


def test_jira_publish_disabled_by_policy(client, auth, demo_repo, jira_state):
    repo = add_repo(client, auth, demo_repo[0])
    configure_jira(client, auth, allow_publish=False)
    inv_id = client.post(
        "/api/jira/investigate", headers=auth, json={"issue_key": "BUG-1", "repo_ids": [repo["id"]]}
    ).json()["investigation_id"]
    r = client.post("/api/jira/publish", headers=auth, json={"investigation_id": inv_id, "approved": True})
    assert r.status_code == 403 and "disabled" in r.text


def test_jira_publish_failure_keeps_report(client, auth, demo_repo, jira_state):
    repo = add_repo(client, auth, demo_repo[0])
    configure_jira(client, auth)
    inv_id = client.post(
        "/api/jira/investigate", headers=auth, json={"issue_key": "BUG-1", "repo_ids": [repo["id"]]}
    ).json()["investigation_id"]
    jira_state["fail_comment"] = True
    r = client.post("/api/jira/publish", headers=auth, json={"investigation_id": inv_id, "approved": True})
    assert r.status_code == 502
    assert client.get(f"/api/investigations/{inv_id}", headers=auth).json()["report_md"]
    assert client.get("/api/jira/publish-events", headers=auth).json()[0]["status"] == "failed"


def test_jira_invalid_issue_key_rejected(client, auth, jira_state):
    configure_jira(client, auth)
    r = client.post("/api/jira/investigate", headers=auth, json={"issue_key": "../../etc", "repo_ids": []})
    assert r.status_code == 502 and not jira_state["requests"]


def test_audit_records_denials(client, auth, session):
    client.post(
        "/api/auth/users",
        headers=auth,
        json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"},
    )
    tok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
        "access_token"
    ]
    r = client.put(
        "/api/jira/connection",
        headers={"Authorization": f"Bearer {tok}"},
        json={"base_url": "https://a.atlassian.net", "email": "a@a.io", "token": "abcd"},
    )
    assert r.status_code == 403
    from repolens.models import AuditEvent

    ev = session.query(AuditEvent).filter_by(action="jira.configure", allowed=False).all()
    assert len(ev) == 1


# ---------- webhooks / ops ----------
def test_github_webhook_signature(client, monkeypatch):
    from repolens.config import get_settings

    monkeypatch.setenv("REPOLENS_GITHUB_WEBHOOK_SECRET", "whsec")
    get_settings.cache_clear()
    body = json.dumps({"repository": {"clone_url": "https://github.com/acme/x.git"}}).encode()
    good = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    hdr = {"X-GitHub-Event": "push", "Content-Type": "application/json"}
    assert (
        client.post(
            "/api/webhooks/github", content=body, headers={**hdr, "X-Hub-Signature-256": "sha256=bad"}
        ).status_code
        == 401
    )
    r = client.post("/api/webhooks/github", content=body, headers={**hdr, "X-Hub-Signature-256": good})
    assert r.status_code == 200 and r.json() == {"reindexing": []}


def test_health_and_metrics(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    client.get("/healthz")
    assert "repolens_http_requests_total" in client.get("/metrics").text
