from datetime import timedelta

from repolens.models import AuditEvent, DocProposal, Investigation, Repository, User, _now
from tests.conftest import register
from tests.test_api import add_repo, run_investigation


def insights(client, h):
    r = client.get("/api/dashboard/insights", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_fresh_workspace_has_nothing_to_worry_about(client, auth):
    d = insights(client, auth)
    assert d["attention"] == [] and d["knowledge"]["pages"] == 0 and d["ai"]["tokens"] == 0 and d["activity"] == []
    assert len(d["investigations_per_day"]) == 14 and d["week"] == {"started": 0, "failed": 0, "confirmed": 0}


def test_things_that_need_attention_are_listed_with_where_to_fix_them(client, auth, demo_repo, session):
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    org = client.get("/api/auth/me", headers=auth).json()["org_id"]
    uid = client.get("/api/auth/me", headers=auth).json()["id"]
    failed = Investigation(
        org_id=org, created_by=uid, query="x", title="broken run", repo_ids=[], timeline=[], status="failed"
    )
    session.add(failed)
    session.add(
        DocProposal(
            org_id=org,
            kind="new",
            source="incident",
            doc_repo_id="",
            doc_path="a.md",
            title="t",
            original="",
            proposed="x",
            status="pending",
            base_hash="h",
        )
    )
    session.query(Repository).filter_by(id=repo["id"]).update({"status": "error"})
    session.commit()
    items = {a["to"].split("?")[0].split("/investigations/")[0]: a for a in insights(client, auth)["attention"]}
    d = insights(client, auth)
    texts = " | ".join(a["text"] for a in d["attention"])
    assert (
        "failed to index" in texts
        and "failed this week" in texts
        and "waiting for your review" in texts
        and "not yet confirmed" in texts
    )
    assert [a["severity"] for a in d["attention"]][:2] == ["high", "high"]  # most serious first
    assert any(a["to"] == f"/investigations/{failed.id}" for a in d["attention"]) and any(
        a["to"] == "/repositories" for a in d["attention"]
    )
    assert d["week"]["started"] >= 2 and items is not None
    client.post(f"/api/investigations/{inv['id']}/review", headers=auth, json={"outcome": "confirmed"})
    assert insights(client, auth)["week"]["confirmed"] == 1


def test_the_daily_trend_counts_finished_and_failed_runs_by_day(client, auth, session):
    org = client.get("/api/auth/me", headers=auth).json()["org_id"]
    for status, ago in (("completed", 0), ("completed", 0), ("failed", 1), ("completed", 40)):
        i = Investigation(org_id=org, created_by="u", query="q", title="q", repo_ids=[], timeline=[], status=status)
        i.created_at = _now() - timedelta(days=ago)
        session.add(i)
    session.commit()
    days = insights(client, auth)["investigations_per_day"]
    assert (
        days[-1]["completed"] == 2 and days[-2]["failed"] == 1 and sum(d["completed"] for d in days) == 2
    )  # the 40-day-old run is outside the window


def test_activity_is_only_for_admins_and_hides_noise(client, auth, session):
    client.post("/api/knowledge/collections", headers=auth, json={"name": "docs"})
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    m = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
            "access_token"
        ]
    }
    act = insights(client, auth)["activity"]
    assert any(a["what"] == "created a documentation collection" and a["target"] == "docs" for a in act)
    assert all(a["what"] != "tested a model" for a in act)
    assert insights(client, m)["activity"] == []  # members do not read the workspace's audit trail
    uid = session.query(User).filter_by(email="admin@acme.io").first()
    session.add(AuditEvent(org_id=uid.org_id, actor_id=uid.id, action="feedback.give", target="x"))
    session.commit()
    assert all("feedback" not in a["what"] for a in insights(client, auth)["activity"])


def test_the_dashboard_is_private_to_the_workspace(client, auth, demo_repo):
    add_repo(client, auth, demo_repo[0])
    other = insights(client, register(client, "z@other.io", "Z"))
    assert other["knowledge"]["chunks"] == 0 and other["attention"] == []
