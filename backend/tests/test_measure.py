import json
import subprocess

import pytest

from repolens.services import measure as svc
from tests.conftest import register
from tests.test_api import add_repo

GOOD = {
    "bugs": {
        "cases": 4,
        "random_top3": 0.1,
        "retrieval_top1": 0.25,
        "retrieval_top3": 0.5,
        "retrieval_top5": 0.5,
        "retrieval_mrr": 0.4,
        "investigation_top1": 0.5,
        "investigation_top3": 0.75,
        "investigation_top5": 0.75,
        "investigation_mrr": 0.6,
        "investigation_top3_ci95": [0.3, 0.95],
        "rows": [{"commit": "abc12345", "report": "fix paging", "files": ["a.py"], "investigation_rank": 1}],
    }
}


def fake(monkeypatch, out="", code=0, err=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr=err)

    monkeypatch.setattr(svc.subprocess, "run", run)
    return calls


@pytest.fixture
def repo(client, auth, demo_repo):
    return add_repo(client, auth, demo_repo[0])


def test_a_run_is_stored_with_caveats_and_runs_without_a_model(client, auth, repo, monkeypatch):
    calls = fake(monkeypatch, "log line\n" + json.dumps(GOOD))
    r = client.post(f"/api/repositories/{repo['id']}/measure", headers=auth, json={"cases": 8})
    assert r.status_code == 202 and r.json()["status"] == "completed"
    res = r.json()["result"]
    assert res["investigation"]["top3"] == 0.75 and res["top3_ci95"] == [0.3, 0.95]
    assert any("small sample" in c for c in res["caveats"])
    assert "-I" in calls[0] and calls[0][-2:] == ["--cases", "8"]
    h = client.get(f"/api/repositories/{repo['id']}/measure", headers=auth).json()
    assert h["can_run"] and h["items"][0]["status"] == "completed"


def test_failures_are_reported_plainly(client, auth, repo, monkeypatch):
    fake(monkeypatch, "", code=1, err="Traceback...\nRuntimeError: boom")
    r = client.post(f"/api/repositories/{repo['id']}/measure", headers=auth, json={})
    assert r.json()["status"] == "failed" and "boom" in r.json()["error"]
    fake(monkeypatch, json.dumps({"bugs": {"cases": 0, "rows": []}}))
    r = client.post(f"/api/repositories/{repo['id']}/measure", headers=auth, json={})
    assert r.json()["status"] == "failed" and "No usable bug fixes" in r.json()["error"]


def test_limits_scope_and_roles(client, auth, repo, monkeypatch):
    fake(monkeypatch, json.dumps(GOOD))
    assert client.post(f"/api/repositories/{repo['id']}/measure", headers=auth, json={"cases": 99}).status_code == 422
    coll = client.post("/api/knowledge/collections", headers=auth, json={"name": "handbook"}).json()
    assert client.post(f"/api/repositories/{coll['id']}/measure", headers=auth, json={}).status_code == 400
    other = register(client, "other@else.io", "Else")
    assert client.post(f"/api/repositories/{repo['id']}/measure", headers=other, json={}).status_code == 404
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "member-pass-12", "role": "viewer"}
    )
    tok = client.post("/api/auth/login", json={"email": "v@acme.io", "password": "member-pass-12"}).json()[
        "access_token"
    ]
    assert (
        client.post(
            f"/api/repositories/{repo['id']}/measure", headers={"Authorization": f"Bearer {tok}"}, json={}
        ).status_code
        == 403
    )


def test_a_repository_without_history_on_disk_cannot_be_measured(client, auth, monkeypatch):
    fake(monkeypatch, json.dumps(GOOD))
    r = client.post(
        "/api/repositories",
        headers=auth,
        json={"name": "remote", "source": "git", "url": "https://example.invalid/x.git"},
    )
    if r.status_code == 201:
        got = client.post(f"/api/repositories/{r.json()['id']}/measure", headers=auth, json={})
        assert got.status_code == 409 and "Index this repository first" in got.json()["detail"]


def test_dashboard_reports_first_steps_for_the_guide(client, auth, repo, monkeypatch):
    d = client.get("/api/dashboard/insights", headers=auth).json()["first_steps"]
    assert d == {"asked": False, "measured": False}
    client.post("/api/chats", headers=auth, json={})
    fake(monkeypatch, json.dumps(GOOD))
    client.post(f"/api/repositories/{repo['id']}/measure", headers=auth, json={})
    assert client.get("/api/dashboard/insights", headers=auth).json()["first_steps"] == {
        "asked": True,
        "measured": True,
    }
