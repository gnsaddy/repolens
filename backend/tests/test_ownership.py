from datetime import UTC, datetime, timedelta

from repolens.models import Change, Organization
from repolens.services import ownership
from tests.conftest import register
from tests.test_knowledge import make_collection

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def add_change(session, org_id, repo_id, sha, author, days_ago, files):
    session.add(
        Change(
            org_id=org_id,
            repo_id=repo_id,
            sha=sha,
            author=author,
            committed_at=NOW - timedelta(days=days_ago),
            message="m",
            files=[{"path": p, "additions": a, "deletions": d} for p, a, d in files],
        )
    )


def seed(client, auth, session):
    col = make_collection(client, auth, "svc")
    org = session.query(Organization).first().id
    rid = col["id"]
    # ana wrote billing long ago, bo maintains it now; ana alone owns auth; a bot touches everything
    add_change(session, org, rid, "1", "ana", 700, [("billing/core.py", 200, 10), ("auth/login.py", 150, 0)])
    add_change(session, org, rid, "2", "bo", 10, [("billing/core.py", 40, 20)])
    add_change(session, org, rid, "3", "bo", 20, [("billing/tax.py", 60, 0), ("README.md", 5, 0)])
    add_change(session, org, rid, "4", "ana", 30, [("auth/login.py", 30, 5)])
    add_change(session, org, rid, "5", "dependabot[bot]", 1, [("billing/core.py", 3, 3)])
    session.commit()
    return rid


def test_recent_maintainers_outrank_original_authors_and_bots_are_ignored(client, auth, session):
    rid = seed(client, auth, session)
    r = ownership.file_owners(session, rid, "billing/core.py", now=NOW)
    names = [o["author"] for o in r["owners"]]
    assert names == ["bo", "ana"] and "dependabot[bot]" not in names
    assert abs(sum(o["share"] for o in r["owners"]) - 1) < 0.01
    assert r["owners"][0]["share"] > 0.6  # the recent change outweighs 700-day-old work
    d = ownership.file_owners(session, rid, "billing", now=NOW)  # directory aggregate
    assert d["owners"][0]["author"] == "bo"
    assert ownership.file_owners(session, rid, "nothing/here.py", now=NOW)["owners"] == []


def test_overview_flags_single_owner_and_stale_areas(client, auth, session):
    rid = seed(client, auth, session)
    from repolens.models import Repository

    o = ownership.overview(session, session.get(Repository, rid), now=NOW + timedelta(days=500))
    areas = {a["area"]: a for a in o["areas"]}
    assert set(areas) == {"billing", "auth", "(root files)"}
    assert areas["auth"]["owners"][0]["author"] == "ana" and areas["auth"]["bus_factor"] == 1
    assert areas["auth"]["risk"] == "stale_owner"  # 500+ days later nobody has touched it
    assert o["summary"]["people"] == 2 and o["summary"]["areas_at_risk"] >= 1
    assert [p["author"] for p in o["people"]][:2] == sorted(
        [p["author"] for p in o["people"]][:2], key=lambda a: -next(x["share"] for x in o["people"] if x["author"] == a)
    )
    fresh = ownership.overview(session, session.get(Repository, rid), now=NOW)
    assert {a["area"]: a["risk"] for a in fresh["areas"]}["auth"] == "single_owner"


def test_experts_merge_several_files(client, auth, session):
    rid = seed(client, auth, session)
    ex = ownership.experts(session, rid, ["billing/core.py", "billing/tax.py", "auth/login.py"], now=NOW)
    assert ex[0]["author"] == "bo" and "billing/tax.py" in ex[0]["files"]
    assert {e["author"] for e in ex} == {"ana", "bo"}


def test_api_and_access(client, auth, session):
    rid = seed(client, auth, session)
    o = client.get("/api/ownership/overview", headers=auth, params={"repo_id": rid}).json()
    assert o["repo"] == "svc" and o["areas"] and o["summary"]["people"] == 2
    p = client.get("/api/ownership/path", headers=auth, params={"repo_id": rid, "path": "billing/core.py"}).json()
    assert p["owners"][0]["author"] in ("bo", "ana")
    e = client.get(
        "/api/ownership/experts", headers=auth, params={"repo_id": rid, "paths": "billing/core.py,auth/login.py"}
    ).json()
    assert e and "files" in e[0]
    other = register(client, "x@other.io", "Other")
    for url, params in (("overview", {}), ("path", {"path": "a"}), ("experts", {"paths": "a"})):
        assert client.get(f"/api/ownership/{url}", headers=other, params={"repo_id": rid, **params}).status_code == 404
    assert client.get("/api/ownership/overview", params={"repo_id": rid}).status_code == 401
