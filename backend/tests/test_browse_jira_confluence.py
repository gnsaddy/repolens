import httpx

from repolens.api import jira as jira_api
from repolens.devtools.fake_confluence import FakeConfluence
from repolens.models import Document
from repolens.services import wiki_import as wi
from tests.conftest import register
from tests.test_api import configure_jira
from tests.test_wiki_confluence import make_transport


def connect_confluence(client, auth, monkeypatch, fake=None, **over):
    fake = fake or FakeConfluence()
    monkeypatch.setattr(wi, "confluence_transport", make_transport(fake))
    body = {
        "base_url": "https://acme.atlassian.net/wiki",
        "deployment": "cloud",
        "email": "bot@acme.io",
        "token": "good-token",
        **over,
    }
    assert client.put("/api/wiki/confluence/connection", headers=auth, json=body).status_code == 200
    return fake


def test_confluence_spaces_and_page_tree_then_import_from_the_returned_urls(client, auth, monkeypatch, session):
    fake = connect_confluence(client, auth, monkeypatch)
    spaces = client.get("/api/wiki/confluence/spaces", headers=auth).json()
    assert {s["key"] for s in spaces} == {"ENG", "FIN"} and next(s for s in spaces if s["key"] == "ENG")[
        "import_url"
    ] == "https://acme.atlassian.net/wiki/spaces/ENG"
    tree = client.get("/api/wiki/confluence/spaces/ENG/pages", headers=auth).json()
    by_id = {p["id"]: p for p in tree["pages"]}
    assert tree["truncated"] is False and {"100", "101", "102", "103"} <= set(by_id)
    assert by_id["101"]["parent_id"] == "100" and by_id["100"]["parent_id"] == "" and by_id["101"]["title"]
    assert by_id["101"]["import_url"] == "https://acme.atlassian.net/wiki/spaces/ENG/pages/101"
    assert all(r[0] == "GET" for r in fake.requests)  # browsing never fetches page bodies or writes
    assert not any("body" in r[1] for r in fake.requests)  # titles only: no page bodies were requested
    # the URLs it returns drive the existing import
    r = client.post(
        "/api/wiki/confluence/import", headers=auth, json={"url": by_id["101"]["import_url"], "mode": "page"}
    )
    assert r.status_code == 202, r.text
    assert session.query(Document).filter_by(repo_id=r.json()["repo_id"]).count() == 1
    r2 = client.post(
        "/api/wiki/confluence/import", headers=auth, json={"url": spaces[0]["import_url"], "mode": "space"}
    )
    assert r2.status_code == 202


def test_confluence_browse_errors_validation_and_roles(client, auth, monkeypatch):
    assert client.get("/api/wiki/confluence/spaces", headers=auth).status_code == 502  # not connected
    fake = connect_confluence(client, auth, monkeypatch)
    assert client.get("/api/wiki/confluence/spaces/bad key!/pages", headers=auth).status_code == 400
    fake.denied_spaces.add("FIN")
    assert {s["key"] for s in client.get("/api/wiki/confluence/spaces", headers=auth).json()} == {
        "ENG"
    }  # hidden spaces stay hidden
    dc = connect_confluence(
        client,
        auth,
        monkeypatch,
        FakeConfluence("datacenter", token="pat-1"),
        base_url="https://fake.corp.io",
        deployment="datacenter",
        email="",
        token="pat-1",
    )
    assert dc
    s = client.get("/api/wiki/confluence/spaces", headers=auth).json()
    assert s[0]["import_url"].startswith("https://fake.corp.io/display/")
    p = client.get("/api/wiki/confluence/spaces/ENG/pages", headers=auth).json()["pages"][0]
    assert p["import_url"].startswith("https://fake.corp.io/pages/viewpage.action?pageId=")
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "Password123!", "role": "member"}
    )
    mtok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "Password123!"}).json()[
        "access_token"
    ]
    assert client.get("/api/wiki/confluence/spaces", headers={"Authorization": f"Bearer {mtok}"}).status_code == 403
    other = register(client, "x@other.io", "Other")
    assert client.get("/api/wiki/confluence/spaces", headers=other).status_code == 502


def test_jira_projects_for_cloud_and_server(client, auth, monkeypatch):
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/project/search"):
            return httpx.Response(
                200,
                json={
                    "values": [
                        {"key": "PAY", "name": "Payments"},
                        {"key": "WEB", "name": "Website"},
                        {"name": "no key"},
                    ]
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(jira_api, "_transport", httpx.MockTransport(handler))
    assert client.get("/api/jira/projects", headers=auth).status_code in (404, 400, 502)  # not connected
    configure_jira(client, auth)
    assert client.get("/api/jira/projects", headers=auth).json() == [
        {"key": "PAY", "name": "Payments"},
        {"key": "WEB", "name": "Website"},
    ]
    other = register(client, "x@other.io", "Other")
    assert client.get("/api/jira/projects", headers=other).status_code in (404, 400)
    monkeypatch.setattr(jira_api, "_transport", httpx.MockTransport(lambda r: httpx.Response(401)))
    assert client.get("/api/jira/projects", headers=auth).status_code == 502
