from repolens.models import CodeChunk, Document, KGNode, Repository
from tests.conftest import register
from tests.test_knowledge import make_collection, upload


def test_delete_a_page_by_path_removes_it_from_search_and_the_graph(client, auth, session):
    col = make_collection(client, auth)
    upload(client, auth, col["id"], {"README.md": "# Alpha\nuses the widget cache", "notes.md": "# Beta\nsecond page"})
    page = client.get("/api/wiki/page", headers=auth, params={"repo_id": col["id"], "path": "README.md"}).json()
    assert page["deletable"] is True and page["managed_by"] is None
    r = client.delete("/api/wiki/page", headers=auth, params={"repo_id": col["id"], "path": "README.md"})
    assert r.status_code == 204
    assert (
        client.get("/api/wiki/page", headers=auth, params={"repo_id": col["id"], "path": "README.md"}).status_code
        == 404
    )
    assert (
        client.get("/api/wiki/page", headers=auth, params={"repo_id": col["id"], "path": "notes.md"}).status_code == 200
    )
    hits = client.post(
        "/api/knowledge/search", headers=auth, json={"query": "widget cache", "repo_ids": [col["id"]]}
    ).json()
    assert all(h["file"] != "README.md" for h in hits)
    assert (
        client.delete("/api/wiki/page", headers=auth, params={"repo_id": col["id"], "path": "README.md"}).status_code
        == 404
    )


def test_synced_pages_cannot_be_deleted_by_hand(client, auth, session):
    col = make_collection(client, auth)
    upload(client, auth, col["id"], {"a.md": "# A\nbody"})
    doc = session.query(Document).filter_by(repo_id=col["id"], path="a.md").one()
    doc.source_type = "confluence"
    session.commit()
    page = client.get("/api/wiki/page", headers=auth, params={"repo_id": col["id"], "path": "a.md"}).json()
    assert page["deletable"] is False and page["managed_by"] == "confluence"
    r = client.delete("/api/wiki/page", headers=auth, params={"repo_id": col["id"], "path": "a.md"})
    assert r.status_code == 409 and "confluence" in r.json()["detail"]
    assert client.delete(f"/api/knowledge/collections/{col['id']}/documents/{doc.id}", headers=auth).status_code == 409


def test_delete_collection_removes_everything_and_is_admin_only(client, auth, session):
    col = make_collection(client, auth, "to-remove")
    upload(client, auth, col["id"], {"a.md": "# A\nbody", "b.md": "# B\n[A](a.md)"})
    assert session.query(KGNode).filter_by(repo_id=col["id"]).count() > 0
    member = register(client, "m@other.io", "Other")
    assert client.delete(f"/api/knowledge/collections/{col['id']}", headers=member).status_code == 404  # other tenant
    assert client.delete(f"/api/knowledge/collections/{col['id']}", headers=auth).status_code == 204
    session.expire_all()
    assert session.get(Repository, col["id"]) is None
    for model in (Document, CodeChunk, KGNode):
        assert session.query(model).filter_by(repo_id=col["id"]).count() == 0
    assert all(r["id"] != col["id"] for r in client.get("/api/repositories", headers=auth).json())
    assert client.delete(f"/api/knowledge/collections/{col['id']}", headers=auth).status_code == 404


def test_code_repository_pages_point_to_removing_the_repository(client, auth, demo_repo):
    path, _ = demo_repo
    repo = client.post(
        "/api/repositories", headers=auth, json={"name": "svc", "source": "local", "url": str(path)}
    ).json()
    r = client.delete("/api/wiki/page", headers=auth, params={"repo_id": repo["id"], "path": "README.md"})
    assert r.status_code == 400 and "Remove the repository" in r.json()["detail"]
    assert client.delete(f"/api/repositories/{repo['id']}", headers=auth).status_code == 204
