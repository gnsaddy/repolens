import hashlib
import hmac
import json

import httpx
import pytest

from repolens.api import github as gh_api
from repolens.models import Change, CodeChunk, GithubConnection, KGNode, PullRequest
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_knowledge import edge_set, graph

TOKEN = "ghp_" + "a" * 36


def make_prs(bad_sha):
    return {
        7: {
            "number": 7,
            "title": "Simplify profile metadata lookup",
            "state": "closed",
            "merged_at": "2026-10-14T11:00:00Z",
            "created_at": "2026-10-13T09:00:00Z",
            "html_url": "https://github.com/acme/user-service/pull/7",
            "user": {"login": "devone"},
            "base": {"ref": "main"},
            "head": {"ref": "feature/BUG-1234-null-guard"},
            "merge_commit_sha": bad_sha,
            "labels": [{"name": "refactor"}],
            "body": "Removes the null guard in `UserProfileService.get_profile`. Fixes BUG-1234.",
        },
        8: {
            "number": 8,
            "title": "docs: runbook for deleted users",
            "state": "open",
            "merged_at": None,
            "created_at": "2026-10-15T09:00:00Z",
            "html_url": "https://github.com/acme/user-service/pull/8",
            "user": {"login": "writer"},
            "base": {"ref": "main"},
            "head": {"ref": "docs/runbook"},
            "merge_commit_sha": None,
            "labels": [],
            "body": "Adds a runbook.",
        },
    }


@pytest.fixture
def github(monkeypatch, demo_repo):
    _, bad_sha = demo_repo
    prs = make_prs(bad_sha)
    state = {"requests": [], "fail": None, "prs": prs}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        p = request.url.path
        if state["fail"]:
            return httpx.Response(state["fail"], headers={"x-ratelimit-remaining": "0"})
        if p == "/user":
            return httpx.Response(200, json={"login": "repolens-bot"})
        if p == "/repos/acme/user-service/pulls":
            return httpx.Response(
                200, json=list(state["prs"].values()) if request.url.params.get("page") == "1" else []
            )
        parts = p.split("/")
        if len(parts) >= 6 and parts[4] == "pulls" and parts[5].isdigit():
            n = int(parts[5])
            if len(parts) == 6:
                return httpx.Response(200, json=state["prs"][n])
            if parts[6] == "files":
                path = "src/user_profile.py" if n == 7 else "README.md"
                return httpx.Response(200, json=[{"filename": path, "additions": 1, "deletions": 4}])
            if parts[6] == "commits":
                return httpx.Response(200, json=[{"sha": bad_sha}] if n == 7 else [])
            if parts[6] == "comments":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "user": {"login": "rev"},
                            "body": "Are we sure the repository never returns None?",
                            "path": "src/user_profile.py",
                        }
                    ]
                    if n == 7
                    else [],
                )
        if len(parts) >= 6 and parts[4] == "issues":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    monkeypatch.setattr(gh_api, "_transport", httpx.MockTransport(handler))
    return state


def connect(client, auth, token=TOKEN):
    r = client.put("/api/github/connection", headers=auth, json={"token": token, "api_base": "https://api.github.com"})
    assert r.status_code == 200, r.text


@pytest.fixture
def synced(client, auth, demo_repo, github):
    repo = add_repo(client, auth, demo_repo[0])
    connect(client, auth)
    r = client.post(f"/api/repositories/{repo['id']}/sync-prs", headers=auth, json={"github_repo": "acme/user-service"})
    assert r.status_code == 202, r.text
    return repo


def test_connection_is_encrypted_and_admin_only(client, auth, session, github):
    connect(client, auth)
    assert TOKEN not in json.dumps(client.get("/api/github/connection", headers=auth).json())
    assert not session.query(GithubConnection).one().token_encrypted.startswith("ghp_")
    assert client.post("/api/github/test", headers=auth).json() == {"ok": True, "user": "repolens-bot"}
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    tok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
        "access_token"
    ]
    assert (
        client.put(
            "/api/github/connection", headers={"Authorization": f"Bearer {tok}"}, json={"token": "x" * 12}
        ).status_code
        == 403
    )
    assert github["requests"][0].headers["authorization"] == f"Bearer {TOKEN}"


def test_sync_requires_connection_and_known_repo(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    assert (
        client.post(
            f"/api/repositories/{repo['id']}/sync-prs", headers=auth, json={"github_repo": "acme/user-service"}
        ).status_code
        == 404
    )
    connect(client, auth)
    r = client.post(f"/api/repositories/{repo['id']}/sync-prs", headers=auth, json={})
    assert r.status_code == 400 and "owner/name" in r.text  # local demo repo has no GitHub origin
    assert (
        client.post(
            f"/api/repositories/{repo['id']}/sync-prs", headers=auth, json={"github_repo": "bad slug"}
        ).status_code
        == 422
    )


def test_sync_stores_prs_and_links_commits(client, auth, synced, session):
    pulls = client.get(f"/api/repositories/{synced['id']}/pulls", headers=auth).json()
    assert [p["number"] for p in pulls] == [8, 7]
    pr7 = next(p for p in pulls if p["number"] == 7)
    assert (
        pr7["state"] == "merged"
        and pr7["jira_keys"] == ["BUG-1234"]
        and pr7["files"] == 1
        and pr7["labels"] == ["refactor"]
    )
    ch = session.query(Change).filter(Change.pr_number == 7).one()
    assert ch.pr_title.startswith("Simplify profile") and session.query(PullRequest).count() == 2
    run = client.get(f"/api/repositories/{synced['id']}/runs", headers=auth).json()[0]
    assert run["status"] == "completed" and run["chunks_written"] == 2


def test_pr_graph_nodes_and_edges_have_github_provenance(client, auth, synced, demo_repo):
    g = graph(client, auth, types="file,doc,section,function,class,commit,pr")
    edges = edge_set(g)
    names = {n["id"]: n for n in g["nodes"]}
    assert ("#7", "MODIFIES", "src/user_profile.py") in edges
    assert ("#7", "MENTIONS", "UserProfileService.get_profile") in edges
    assert ("#7", "INCLUDES", demo_repo[1]) in edges  # commit nodes are keyed by their full SHA
    for e in g["edges"]:
        if names[e["source"]]["type"] == "pr":
            assert e["provenance"] == "github"


def test_prs_are_searchable_and_cited(client, auth, synced):
    hits = client.post(
        "/api/knowledge/search", headers=auth, json={"query": "null guard repository returns None", "kinds": ["pr"]}
    ).json()
    assert hits and hits[0]["kind"] == "pr" and hits[0]["file"] == "pull/7"
    assert any(c["label"] in ("modifies", "mentions") for c in hits[0]["connections"])
    a = client.post("/api/knowledge/ask", headers=auth, json={"query": "why was the null guard removed?"}).json()
    assert any(s["kind"] == "pr" for s in a["sources"])


def test_pr_data_survives_reindex_and_resync_is_idempotent(client, auth, synced, github, session):
    client.post(f"/api/repositories/{synced['id']}/index", headers=auth)
    assert session.query(CodeChunk).filter_by(chunk_type="pr").count() == 2
    assert session.query(KGNode).filter_by(type="pr").count() == 2
    assert ("#7", "MODIFIES", "src/user_profile.py") in edge_set(
        graph(client, auth, types="file,pr,function,class,doc,section")
    )
    github["prs"][7]["body"] += " Update: reverted partially."
    client.post(f"/api/repositories/{synced['id']}/sync-prs", headers=auth, json={"github_repo": "acme/user-service"})
    assert session.query(CodeChunk).filter_by(chunk_type="pr").count() == 2 and session.query(PullRequest).count() == 2
    session.expire_all()
    assert "reverted partially" in session.query(PullRequest).filter_by(number=7).one().body


def test_rate_limit_is_recorded_and_token_not_leaked(client, auth, demo_repo, github):
    repo = add_repo(client, auth, demo_repo[0])
    connect(client, auth)
    github["fail"] = 403
    client.post(f"/api/repositories/{repo['id']}/sync-prs", headers=auth, json={"github_repo": "acme/user-service"})
    run = client.get(f"/api/repositories/{repo['id']}/runs", headers=auth).json()[0]
    assert run["status"] == "failed" and "rate limit" in run["error"] and TOKEN not in json.dumps(run)


def test_investigation_cites_the_pull_request(client, auth, synced):
    from tests.demo_repo import BUG_REPORT

    r = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [synced["id"]]})
    inv = client.get(f"/api/investigations/{r.json()['id']}", headers=auth).json()
    assert inv["status"] == "completed" and inv["verification"]["valid"] is True
    pr = next(e for e in inv["hypotheses"][0]["evidence"] if e["type"] == "pr")
    assert pr["number"] == 7 and pr["jira_keys"] == ["BUG-1234"]
    assert '[#7](https://github.com/acme/user-service/pull/7) "Simplify profile metadata lookup"' in inv["report_md"]


def test_pull_request_webhook_syncs_single_pr(client, auth, demo_repo, github, monkeypatch, session):
    from repolens.config import get_settings

    monkeypatch.setenv("REPOLENS_GITHUB_WEBHOOK_SECRET", "whsec")
    get_settings.cache_clear()
    connect(client, auth)
    r = client.post(
        "/api/repositories",
        headers=auth,
        json={"name": "svc", "source": "git", "url": "https://github.com/acme/user-service.git"},
    )
    assert r.status_code == 201  # clone fails offline; the repo row is enough to match the webhook
    body = json.dumps(
        {
            "action": "opened",
            "pull_request": {"number": 7},
            "repository": {"clone_url": "https://github.com/acme/user-service.git", "full_name": "acme/user-service"},
        }
    ).encode()
    sig = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    hdr = {"X-GitHub-Event": "pull_request", "Content-Type": "application/json"}
    assert (
        client.post(
            "/api/webhooks/github", content=body, headers={**hdr, "X-Hub-Signature-256": "sha256=bad"}
        ).status_code
        == 401
    )
    out = client.post("/api/webhooks/github", content=body, headers={**hdr, "X-Hub-Signature-256": sig}).json()
    assert len(out["pr_synced"]) == 1
    assert session.query(PullRequest).filter_by(number=7).count() == 1 and session.query(PullRequest).count() == 1


def test_pulls_are_tenant_isolated(client, auth, synced):
    other = register(client, "x@other.io", "Other")
    assert client.get(f"/api/repositories/{synced['id']}/pulls", headers=other).status_code == 404
    assert client.post(f"/api/repositories/{synced['id']}/sync-prs", headers=other, json={}).status_code == 404


def test_api_base_must_be_https_outside_dev(client, auth, monkeypatch):
    from repolens.config import get_settings

    monkeypatch.setenv("REPOLENS_ENV", "prod")
    get_settings.cache_clear()
    r = client.put(
        "/api/github/connection", headers=auth, json={"token": TOKEN, "api_base": "http://ghe.internal/api/v3"}
    )
    assert r.status_code == 400
