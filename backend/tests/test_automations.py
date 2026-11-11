"""Automations: a failed CI run starts investigations by itself, only when an admin turned it on."""

import hashlib
import hmac
import json

import httpx
import pytest

from repolens.api import github as gh_api
from repolens.config import get_settings
from repolens.models import AgentRun, AuditEvent, Investigation
from tests.test_chat import make_user
from tests.test_github import TOKEN, connect

CLONE = "https://github.com/acme/shop.git"
PYTEST_LOG = (
    "============ FAILURES ============\n"
    "______ test_total_with_discount ______\n\n"
    "    def test_total_with_discount():\n"
    ">       assert total(100, 10) == 90\n"
    "E       assert 110 == 90\n\n"
    "tests/test_pricing.py:12: AssertionError\n"
    "=========== short test summary info ===========\n"
    "FAILED tests/test_pricing.py::test_total_with_discount - assert 110 == 90\n"
    "=========== 1 failed, 3 passed in 0.12s ===========\n"
)


@pytest.fixture
def ci(client, auth, monkeypatch):
    """A git repository named after acme/shop, a GitHub connection, a mock GitHub, and a way to send events."""
    monkeypatch.setenv("REPOLENS_GITHUB_WEBHOOK_SECRET", "whsec")
    get_settings.cache_clear()
    started: list[str] = []
    monkeypatch.setattr("repolens.services.automations.submit", started.append)  # no model work: only what was started
    state = {"log": PYTEST_LOG, "jobs": [{"id": 11, "name": "tests", "conclusion": "failure"}], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        p = request.url.path
        if request.url.host == "logs.example.net":
            return httpx.Response(200, content=state["log"].encode())
        if p == "/user":
            return httpx.Response(200, json={"login": "bot"})
        if p.endswith("/actions/runs/501/jobs"):
            return httpx.Response(200, json={"jobs": state["jobs"]})
        if p.endswith("/actions/jobs/11/logs"):
            return httpx.Response(302, headers={"location": "https://logs.example.net/signed/abc?sig=1"})
        return httpx.Response(404)

    monkeypatch.setattr(gh_api, "_transport", httpx.MockTransport(handler))
    connect(client, auth)
    r = client.post("/api/repositories", headers=auth, json={"name": "shop", "source": "git", "url": CLONE})
    assert r.status_code == 201
    state["repo"] = r.json()
    state["started"] = started

    def send(run_id=501, branch="main", conclusion="failure", action="completed", attempt=1, event="workflow_run"):
        body = json.dumps(
            {
                "action": action,
                "workflow_run": {
                    "id": run_id,
                    "name": "CI",
                    "conclusion": conclusion,
                    "head_branch": branch,
                    "run_attempt": attempt,
                    "html_url": f"https://github.com/acme/shop/actions/runs/{run_id}",
                },
                "repository": {"clone_url": CLONE, "full_name": "acme/shop"},
            }
        ).encode()
        sig = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
        hdr = {"X-GitHub-Event": event, "Content-Type": "application/json", "X-Hub-Signature-256": sig}
        r = client.post("/api/webhooks/github", content=body, headers=hdr)
        assert r.status_code == 200, r.text
        return r.json()

    state["send"] = send
    return state


def turn_on(client, auth, repo_id="all", **body):
    r = client.put(f"/api/automations/rules/{repo_id}", headers=auth, json={"enabled": True, **body})
    assert r.status_code == 200, r.text


def test_nothing_starts_until_an_admin_turns_it_on(client, auth, ci, session):
    assert ci["send"]() == {"automation": []}
    assert session.query(AgentRun).count() == 0 and session.query(Investigation).count() == 0


def test_a_failed_run_starts_one_investigation_per_failing_test_and_says_so(client, auth, ci, session):
    turn_on(client, auth)
    out = ci["send"]()
    assert len(out["automation"]) == 1
    session.expire_all()
    inv = session.query(Investigation).one()
    assert inv.source == "ci_auto" and inv.title.startswith("CI failure:") and "test_total_with_discount" in inv.query
    assert inv.repo_ids == [ci["repo"]["id"]] and ci["started"] == [inv.id]
    run = session.query(AgentRun).one()
    assert run.status == "started" and run.investigation_ids == [inv.id] and run.link.endswith("/actions/runs/501")
    ev = session.query(AuditEvent).filter_by(action="automation.ci_failure").one()
    assert ev.target == "shop" and ev.detail["started"] == 1
    listed = client.get("/api/investigations", headers=auth).json()
    assert listed[0]["source"] == "ci_auto"


def test_the_token_is_not_sent_to_the_address_the_log_is_read_from(client, auth, ci):
    turn_on(client, auth)
    ci["send"]()
    log_calls = [r for r in ci["requests"] if r.url.host == "logs.example.net"]
    api_calls = [r for r in ci["requests"] if r.url.host == "api.github.com"]
    assert log_calls and all("authorization" not in r.headers for r in log_calls)
    assert api_calls and all(TOKEN in r.headers["authorization"] for r in api_calls)


def test_the_same_run_is_handled_once_even_if_github_sends_it_again(client, auth, ci, session):
    turn_on(client, auth)
    ci["send"]()
    assert ci["send"]() == {"automation": []}
    assert session.query(Investigation).count() == 1 and session.query(AgentRun).count() == 1
    assert len(ci["send"](attempt=2)["automation"]) == 1  # a re run is a new attempt


def test_a_run_that_passed_or_is_not_finished_is_ignored(client, auth, ci, session):
    turn_on(client, auth)
    assert ci["send"](conclusion="success") == {"automation": "ignored"}
    assert ci["send"](action="requested", conclusion="failure") == {"automation": "ignored"}
    assert session.query(AgentRun).count() == 0


def test_only_the_repositorys_branch_unless_all_branches_is_on(client, auth, ci, session):
    turn_on(client, auth)
    ci["send"](branch="feature/x")
    run = session.query(AgentRun).one()
    assert run.status == "skipped" and "Not the" in run.reason and session.query(Investigation).count() == 0
    turn_on(client, auth, all_branches=True)
    ci["send"](run_id=502, branch="feature/x")  # the mock GitHub knows no jobs for run 502, so it is tried and fails
    runs = {r.event_key: r for r in session.query(AgentRun).all()}
    assert runs["502-1"].status == "failed" and "GitHub said no" in runs["502-1"].reason


def test_the_daily_limit_stops_more_than_it_allows(client, auth, ci, session):
    turn_on(client, auth, daily_limit=1)
    ci["send"]()
    ci["send"](run_id=501, attempt=2)
    session.expire_all()
    assert session.query(Investigation).count() == 1
    last = session.query(AgentRun).order_by(AgentRun.created_at.desc()).first()
    assert last.status == "skipped" and "limit of 1" in last.reason


def test_a_log_with_no_test_failure_still_gets_an_investigation_from_its_end(client, auth, ci, session):
    ci["log"] = "step 1 ok\nstep 2 ok\nBuild failed: could not resolve dependency left-pad@^9\n"
    turn_on(client, auth)
    ci["send"]()
    inv = session.query(Investigation).one()
    assert "left-pad" in inv.query and inv.source == "ci_auto"


def test_a_secret_in_the_log_is_never_stored(client, auth, ci, session):
    ci["log"] = "Build failed. token=" + TOKEN + "\nDeploy aborted\n"
    turn_on(client, auth)
    ci["send"]()
    assert TOKEN not in session.query(Investigation).one().query


def test_no_github_connection_is_reported_not_hidden(client, auth, ci, session):
    turn_on(client, auth)
    from repolens.models import GithubConnection

    session.query(GithubConnection).delete()
    session.commit()
    ci["send"]()
    run = session.query(AgentRun).one()
    assert run.status == "failed" and "not connected" in run.reason


def test_a_job_that_cannot_be_listed_is_reported_with_the_reason(client, auth, ci, session):
    turn_on(client, auth)
    ci["jobs"] = []
    ci["send"]()
    run = session.query(AgentRun).one()
    assert run.status == "skipped" and "no failed job" in run.reason


def test_it_runs_as_the_admin_who_turned_it_on_and_stops_if_that_person_is_gone(client, auth, ci, session):
    from repolens.models import User

    turn_on(client, auth)
    session.query(User).filter_by(email="admin@acme.io").one().active = False
    session.commit()
    ci["send"]()
    run = session.query(AgentRun).one()
    assert (
        run.status == "skipped"
        and "no longer an active admin" in run.reason
        and session.query(Investigation).count() == 0
    )


def test_only_admins_see_or_change_the_rules_and_only_for_their_own_repositories(client, auth, ci):
    member = make_user(client, auth, "dev@acme.io")
    assert client.get("/api/automations", headers=member).status_code == 403
    assert client.put("/api/automations/rules/all", headers=member, json={"enabled": True}).status_code == 403
    other = make_user(client, auth, "other@acme.io", "admin")
    assert (
        client.put(f"/api/automations/rules/{ci['repo']['id']}", headers=other, json={"enabled": True}).status_code
        == 200
    )
    from tests.conftest import register

    stranger = register(client, "x@other.io", "Other")
    assert (
        client.put(f"/api/automations/rules/{ci['repo']['id']}", headers=stranger, json={"enabled": True}).status_code
        == 404
    )
    seen = client.get("/api/automations", headers=stranger).json()
    assert seen["repositories"] == [] and seen["runs"] == []


def test_the_overview_lists_repositories_rules_and_recent_runs(client, auth, ci):
    turn_on(client, auth, ci["repo"]["id"], daily_limit=3)
    ci["send"]()
    o = client.get("/api/automations", headers=auth).json()
    row = o["repositories"][0]
    assert row["name"] == "shop" and row["enabled"] is True and row["daily_limit"] == 3 and row["own_rule"] is True
    assert o["all"]["enabled"] is False and o["used_today"] == 1
    assert o["runs"][0]["status"] == "started" and o["runs"][0]["repo"] == "shop"


def test_a_bad_limit_is_refused(client, auth, ci):
    assert (
        client.put("/api/automations/rules/all", headers=auth, json={"enabled": True, "daily_limit": 0}).status_code
        == 422
    )
    assert (
        client.put("/api/automations/rules/all", headers=auth, json={"enabled": True, "daily_limit": 500}).status_code
        == 422
    )


def test_the_log_is_read_from_an_https_address_only(client, auth, ci, session, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 11, "name": "t", "conclusion": "failure"}]})
        return httpx.Response(302, headers={"location": "http://evil.example/x"})

    monkeypatch.setattr(gh_api, "_transport", httpx.MockTransport(handler))
    turn_on(client, auth)
    ci["send"]()
    run = session.query(AgentRun).one()
    assert run.status == "failed" and "not https" in run.reason
