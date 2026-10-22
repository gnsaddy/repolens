import httpx
import pytest

from repolens.api import github as gh_api
from repolens.models import Organization, Repository
from tests.conftest import register


def repo(name, owner="acme", private=False, **kw):
    return {
        "full_name": f"{owner}/{name}",
        "name": name,
        "owner": {"login": owner},
        "description": kw.get("desc", ""),
        "private": private,
        "default_branch": "main",
        "language": "Python",
        "stargazers_count": 3,
        "open_issues_count": 1,
        "pushed_at": "2026-09-01T10:00:00Z",
        "html_url": f"https://github.com/{owner}/{name}",
        "clone_url": f"https://github.com/{owner}/{name}.git",
    }


@pytest.fixture
def gh(monkeypatch):
    state = {
        "calls": [],
        "repos": [repo("orders", private=True, desc="Order service"), repo("web"), repo("billing", owner="other-org")],
        "fail": None,
    }

    def handler(r: httpx.Request) -> httpx.Response:
        state["calls"].append((r.url.path, dict(r.url.params), r.headers.get("authorization")))
        if state["fail"]:
            return httpx.Response(state["fail"], json={"message": "x"})
        p = r.url.path
        if p == "/user/repos":
            page = int(r.url.params.get("page", 1))
            return httpx.Response(200, json=state["repos"] if page == 1 else [])
        if p == "/repos/acme/orders/pulls":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 7,
                        "title": "Fix rounding",
                        "state": "closed",
                        "merged_at": "2026-09-02T00:00:00Z",
                        "user": {"login": "ana"},
                        "head": {"ref": "fix-round"},
                        "base": {"ref": "main"},
                        "labels": [{"name": "bug"}],
                        "html_url": "https://github.com/acme/orders/pull/7",
                        "updated_at": "2026-09-02T00:00:00Z",
                    },
                    {
                        "number": 8,
                        "title": "Add retries",
                        "state": "open",
                        "draft": True,
                        "user": {"login": "bo"},
                        "head": {"ref": "retry"},
                        "base": {"ref": "main"},
                        "labels": [],
                        "html_url": "https://github.com/acme/orders/pull/8",
                        "updated_at": "2026-09-03T00:00:00Z",
                    },
                ],
            )
        if p == "/repos/acme/orders/issues":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "Totals wrong",
                        "state": "open",
                        "comments": 4,
                        "user": {"login": "cy"},
                        "labels": [{"name": "bug"}],
                        "html_url": "https://github.com/acme/orders/issues/1",
                        "updated_at": "2026-09-01T00:00:00Z",
                    },
                    {"number": 7, "title": "Fix rounding", "state": "closed", "pull_request": {"url": "x"}},
                ],
            )
        if p == "/repos/acme/orders/branches":
            return httpx.Response(200, json=[{"name": "main", "protected": True}, {"name": "develop"}])
        return httpx.Response(404, json={"message": "Not Found"})

    monkeypatch.setattr(gh_api, "_transport", httpx.MockTransport(handler))
    return state


def connect(client, auth):
    assert (
        client.put("/api/github/connection", headers=auth, json={"token": "github_pat_" + "a" * 30}).status_code == 200
    )


def test_browse_lists_repositories_with_search_paging_and_added_marker(client, auth, gh, session):
    assert client.get("/api/github/repos", headers=auth).status_code == 404  # not connected yet
    connect(client, auth)
    d = client.get("/api/github/repos", headers=auth).json()
    assert d["total"] == 3 and [r["full_name"] for r in d["items"]] == ["acme/orders", "acme/web", "other-org/billing"]
    first = d["items"][0]
    assert (
        first["private"] is True and first["clone_url"].endswith("acme/orders.git") and first["added_repo_id"] is None
    )
    assert all(c[2].startswith("Bearer github_pat_") for c in gh["calls"])
    assert [
        r["full_name"] for r in client.get("/api/github/repos", headers=auth, params={"q": "ORDER"}).json()["items"]
    ] == ["acme/orders"]
    assert (
        client.get("/api/github/repos", headers=auth, params={"q": "service"}).json()["total"] == 1
    )  # description search
    paged = client.get("/api/github/repos", headers=auth, params={"per_page": 2, "page": 2}).json()
    assert [r["full_name"] for r in paged["items"]] == ["other-org/billing"] and paged["total"] == 3
    # a repository already added under the same GitHub address is marked
    org = session.query(Organization).first().id
    row = Repository(
        org_id=org, name="orders-svc", source="git", url="https://github.com/Acme/orders.git", status="ready"
    )
    session.add(row)
    session.commit()
    marked = client.get("/api/github/repos", headers=auth, params={"q": "orders"}).json()["items"][0]["added_repo_id"]
    assert marked == row.id


def test_pull_requests_issues_and_branches(client, auth, gh):
    connect(client, auth)
    prs = client.get("/api/github/repos/acme/orders/pulls", headers=auth, params={"state": "all"}).json()
    assert [(p["number"], p["state"], p["draft"]) for p in prs] == [(7, "merged", False), (8, "open", True)]
    assert prs[0]["labels"] == ["bug"] and prs[0]["author"] == "ana" and prs[0]["head"] == "fix-round"
    issues = client.get("/api/github/repos/acme/orders/issues", headers=auth).json()
    assert [i["number"] for i in issues] == [1]  # the pull request that GitHub lists among issues is excluded
    assert client.get("/api/github/repos/acme/orders/branches", headers=auth).json() == [
        {"name": "main", "protected": True},
        {"name": "develop", "protected": False},
    ]
    assert client.get("/api/github/repos/acme/orders/pulls", headers=auth, params={"state": "nope"}).status_code == 400
    assert client.get("/api/github/repos/acme/missing/pulls", headers=auth).status_code == 404
    assert client.get("/api/github/repos/a..b/x y/pulls", headers=auth).status_code in (400, 404)


def test_token_problems_are_explained_and_access_is_isolated(client, auth, gh):
    connect(client, auth)
    gh["fail"] = 401
    r = client.get("/api/github/repos", headers=auth)
    assert r.status_code == 502 and "rejected the token" in r.json()["detail"] and "github_pat_" not in r.text
    gh["fail"] = None
    other = register(client, "x@other.io", "Other")
    assert client.get("/api/github/repos", headers=other).status_code == 404  # no connection in that organisation
    assert client.get("/api/github/repos").status_code == 401
