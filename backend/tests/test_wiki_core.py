from tests.conftest import register
from tests.test_wiki_browse import member, wiki  # noqa: F401  (fixture)
from tests.test_wiki_confluence import fake  # noqa: F401  (fixture)


def test_search_finds_pages_by_meaning_one_row_per_page(client, auth, wiki):  # noqa: F811
    rows = client.get("/api/wiki/search", headers=auth, params={"q": "deploy checklist migrations restart api"}).json()
    assert rows and all({"repo", "repo_id", "path", "title", "snippet"} <= set(r) for r in rows)
    assert len({(r["repo_id"], r["path"]) for r in rows}) == len(rows)
    assert any(r["path"] in ("copy-a.md", "copy-b.md") for r in rows)
    assert client.get("/api/wiki/search", headers=auth, params={"q": "x"}).status_code == 422


def test_search_never_returns_pages_the_user_cannot_see(client, auth, wiki):  # noqa: F811
    h, _ = member(client, auth)
    client.put(
        f"/api/repositories/{wiki['notes']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    rows = client.get("/api/wiki/search", headers=h, params={"q": "deploy checklist migrations"}).json()
    assert all(r["repo_id"] != wiki["notes"] for r in rows)
    assert (
        client.get(
            "/api/wiki/search", headers=register(client, "o@other.io", "O"), params={"q": "deploy checklist"}
        ).json()
        == []
    )


def test_pages_can_be_created_edited_and_read_back(client, auth, wiki):  # noqa: F811
    notes = wiki["notes"]
    r = client.put(
        "/api/wiki/page",
        headers=auth,
        json={"repo_id": notes, "path": "runbooks/restart.md", "content": "# Restart\n\nDrain the queue first.\n"},
    )
    assert r.status_code == 200 and r.json()["created"] is True
    src = client.get(
        "/api/wiki/page/source", headers=auth, params={"repo_id": notes, "path": "runbooks/restart.md"}
    ).json()
    assert src["editable"] is True and "Drain the queue" in src["content"]
    r = client.put(
        "/api/wiki/page",
        headers=auth,
        json={"repo_id": notes, "path": "runbooks/restart.md", "content": "# Restart\n\nDrain, then stop.\n"},
    )
    assert r.json()["created"] is False
    assert (
        "then stop"
        in client.get(
            "/api/wiki/page/source", headers=auth, params={"repo_id": notes, "path": "runbooks/restart.md"}
        ).json()["content"]
    )


def test_page_editing_guards(client, auth, wiki):  # noqa: F811
    notes = wiki["notes"]

    def put(**kw):
        body = {"repo_id": notes, "path": "a.md", "content": "x", **kw}
        return client.put("/api/wiki/page", headers=auth, json=body)

    assert put(path="../evil.md").status_code == 400
    assert put(path="notes.exe").status_code == 400
    assert put(content="a\x00b").status_code == 400
    assert (
        client.put(
            "/api/wiki/page", headers=auth, json={"repo_id": wiki["code"], "path": "a.md", "content": "x"}
        ).status_code
        == 400
    )  # code repo
    s = client.put(
        "/api/wiki/page", headers=auth, json={"repo_id": notes, "path": "k.md", "content": "token ghp_" + "a" * 36}
    ).json()
    assert s["secrets_redacted"] >= 1
    # pages synced from Confluence are read-only here
    tree = client.get("/api/wiki/tree", headers=auth, params={"repo_id": wiki["confluence"]}).json()
    path = next(n["path"] for n in _walk(tree) if n.get("path"))
    src = client.get("/api/wiki/page/source", headers=auth, params={"repo_id": wiki["confluence"], "path": path}).json()
    assert src["editable"] is False and "Confluence" in src["reason"] or "confluence" in src["reason"].lower()
    assert (
        client.put(
            "/api/wiki/page", headers=auth, json={"repo_id": wiki["confluence"], "path": path, "content": "x"}
        ).status_code
        == 409
    )
    assert (
        client.get("/api/wiki/page/source", headers=auth, params={"repo_id": notes, "path": "missing.md"}).status_code
        == 404
    )


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children", []))


def test_atlas_maps_modules_with_symbols_and_coverage(client, auth, wiki):  # noqa: F811
    a = client.get("/api/wiki/atlas", headers=auth, params={"repo_id": wiki["code"]}).json()
    assert a["summary"]["modules"] >= 1 and a["summary"]["public_symbols"] >= 2
    src = next(m for m in a["modules"] if m["module"] == "src")
    names = {s["name"] for s in src["top_symbols"]}
    assert "UserProfileService" in names
    assert src["files"] >= 1 and "py" in src["languages"] and src["coverage"] is not None
    assert client.get("/api/wiki/atlas", headers=auth, params={"repo_id": wiki["notes"]}).status_code == 400
    assert (
        client.get(
            "/api/wiki/atlas", headers=register(client, "o2@other.io", "O2"), params={"repo_id": wiki["code"]}
        ).status_code
        == 404
    )
