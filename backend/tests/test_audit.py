from tests.conftest import register


def make(client, auth, email="m@acme.io", role="member"):
    client.post("/api/auth/users", headers=auth, json={"email": email, "password": "member-pass-1", "role": role})
    return {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": email, "password": "member-pass-1"}).json()["access_token"]
    }


def test_admins_can_read_who_did_what_and_what_was_refused(client, auth):
    m = make(client, auth, role="viewer")
    client.post("/api/knowledge/collections", headers=m, json={"name": "nope"})  # refused: a viewer cannot write
    client.post("/api/knowledge/collections", headers=auth, json={"name": "docs"})
    client.post("/api/auth/login", json={"email": "m@acme.io", "password": "wrong-pass-12"})
    log = client.get("/api/audit", headers=auth).json()
    assert log["total"] >= 3 and {"id", "at", "actor", "action", "target", "allowed", "detail"} <= set(log["items"][0])
    refused = client.get("/api/audit", headers=auth, params={"allowed": "no"}).json()["items"]
    assert any(e["actor"] == "m@acme.io" and e["action"] == "collection.create" for e in refused)
    assert any(e["action"] == "auth.login_failed" for e in refused)
    created = client.get("/api/audit", headers=auth, params={"action": "collection", "allowed": "yes"}).json()["items"]
    assert created and all(e["action"].startswith("collection") and e["allowed"] for e in created)
    assert client.get("/api/audit", headers=auth, params={"q": "docs"}).json()["total"] >= 1
    page = client.get("/api/audit", headers=auth, params={"limit": 1, "offset": 1}).json()
    assert len(page["items"]) == 1 and page["total"] >= log["total"]  # reading the log is itself recorded


def test_facets_and_csv_export_and_formula_safety(client, auth, session):
    from repolens.models import AuditEvent, User

    client.post("/api/knowledge/collections", headers=auth, json={"name": "docs"})
    u = session.query(User).first()
    session.add(AuditEvent(org_id=u.org_id, actor_id=u.id, action="x.test", target='=HYPERLINK("http://x")'))
    session.commit()
    f = client.get("/api/audit/facets", headers=auth).json()
    assert {"collection", "x"} <= set(f["groups"]) and any(x["email"] == "admin@acme.io" for x in f["users"])
    r = client.get("/api/audit/export.csv", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.splitlines()[0] == "time,actor,action,target,allowed,detail"
    assert "'=HYPERLINK" in r.text and ",=HYPERLINK" not in r.text  # a cell never starts with = (spreadsheet formulas)


def test_the_log_is_admin_only_and_private_to_the_workspace(client, auth):
    m = make(client, auth)
    for url in ("/api/audit", "/api/audit/facets", "/api/audit/export.csv"):
        assert client.get(url, headers=m).status_code == 403, url
    assert (
        client.get("/api/audit", headers=register(client, "z@other.io", "Z")).json()["items"][0]["actor"]
        == "z@other.io"
    )
    assert all(
        "acme" not in str(e)
        for e in client.get("/api/audit", headers=register(client, "y@other.io", "Y")).json()["items"]
    )


def test_secrets_in_targets_or_details_are_masked(client, auth, session):
    from repolens.models import AuditEvent, User

    u = session.query(User).first()
    session.add(
        AuditEvent(
            org_id=u.org_id,
            actor_id=u.id,
            action="x.test",
            target="token ghp_" + "a" * 36,
            detail={"key": "sk-ant-" + "b" * 30},
        )
    )
    session.commit()
    body = str(client.get("/api/audit", headers=auth, params={"action": "x.test"}).json())
    assert "ghp_" + "a" * 36 not in body and "b" * 30 not in body
