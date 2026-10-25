from datetime import UTC, datetime, timedelta

from repolens.models import Investigation
from repolens.services import investigations as svc
from tests.test_api import add_repo, run_investigation


def stuck(session, org_id, status="running", minutes=30):
    inv = Investigation(org_id=org_id, created_by="u", query="q", title="q", repo_ids=[], timeline=[], status=status)
    inv.created_at = datetime.now(UTC) - timedelta(minutes=minutes)
    session.add(inv)
    session.commit()
    return inv


def test_runs_left_running_are_failed_instead_of_spinning_forever(client, auth, session):
    org = client.get("/api/auth/me", headers=auth).json()["org_id"]
    old, fresh = stuck(session, org, "running", 30), stuck(session, org, "queued", 1)
    assert svc.reap_stuck(session) == 1  # only the one well past the limit
    session.refresh(old)
    session.refresh(fresh)
    assert old.status == "failed" and "stopped" in old.error and old.finished_at is not None
    assert fresh.status == "queued"
    assert (
        svc.reap_stuck(session, restart=True) == 0
    )  # a minute-old run is not touched by the short restart grace either


def test_after_a_restart_unfinished_runs_are_failed_quickly(client, auth, session):
    org = client.get("/api/auth/me", headers=auth).json()["org_id"]
    a, b = stuck(session, org, "running", 5), stuck(session, org, "queued", 0)
    assert svc.reap_stuck(session, restart=True) == 1
    session.refresh(a)
    session.refresh(b)
    assert a.status == "failed" and "restart" in a.error and b.status == "queued"


def test_a_run_over_the_time_limit_stops_and_can_be_run_again(client, auth, demo_repo, monkeypatch):
    monkeypatch.setenv("REPOLENS_INVESTIGATION_TIMEOUT_S", "-1")  # every step is already over the limit
    from repolens import config

    config.get_settings.cache_clear()
    repo = add_repo(client, auth, demo_repo[0])
    inv = client.post(
        "/api/investigations", headers=auth, json={"query": "KeyError in get_profile", "repo_ids": [repo["id"]]}
    ).json()
    d = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    assert d["status"] == "failed" and "longer than" in d["error"]
    monkeypatch.setenv("REPOLENS_INVESTIGATION_TIMEOUT_S", "300")
    config.get_settings.cache_clear()
    r = client.post(f"/api/investigations/{inv['id']}/rerun", headers=auth)
    assert r.status_code == 202
    d = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    assert d["status"] == "completed" and d["error"] is None and d["timeline"]
    assert client.post(f"/api/investigations/{inv['id']}/rerun", headers=auth).status_code == 409  # only failed ones


def test_rerun_is_scoped_to_the_workspace(client, auth, demo_repo):
    from tests.conftest import register

    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    assert (
        client.post(f"/api/investigations/{inv['id']}/rerun", headers=register(client, "r@other.io", "R")).status_code
        == 404
    )
