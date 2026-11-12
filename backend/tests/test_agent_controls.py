"""The agent's controls (pause, token budget, cancel, the trace), retries when GitHub is down, its health, and more triggers."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx

from repolens.api import github as gh_api
from repolens.config import get_settings
from repolens.models import AgentDraft, AgentRun, AuditEvent, DocProposal, DriftAlert, Investigation, PersonaRun
from repolens.services import agent_controls, agent_triggers
from tests.test_automations import (  # noqa: F401  (fixtures and helpers)
    CLONE,
    ci,
    inbox,
    index_it,
    make_user,
    model,
    reviewed,
    turn_on,
    turn_on_pr,
)
from tests.test_proposals import drifted  # noqa: F401
from tests.test_risk import shop  # noqa: F401  (fixture)


def controls(client, auth, **body):
    r = client.put("/api/automations/controls", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def runs(client, auth):
    return client.get("/api/automations", headers=auth).json()["runs"]


def post_event(client, event, payload):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": event, "Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ pause
def test_a_paused_agent_starts_nothing_by_itself_but_an_admin_can_still_run_it(client, auth, ci, session):
    turn_on(client, auth)
    assert controls(client, auth, paused=True)["paused"] is True
    ci["send"]()
    run = session.query(AgentRun).one()
    assert run.status == "skipped" and "paused by an admin" in run.reason and session.query(Investigation).count() == 0
    (r,) = runs(client, auth)
    assert r["retry"] == "run_anyway"
    assert client.post(f"/api/automations/runs/{r['id']}/retry", headers=auth).status_code == 200
    session.expire_all()
    assert session.query(Investigation).count() == 1  # a person's own go ahead is not held back
    controls(client, auth, paused=False)
    assert len(ci["send"](attempt=2)["automation"]) == 1


def test_a_paused_agent_does_not_review_a_pull_request_either(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    controls(client, auth, paused=True)
    ci["send_pr"]()
    run = session.query(AgentRun).one()
    assert run.status == "skipped" and "paused" in run.reason and session.query(PersonaRun).count() == 0


# ------------------------------------------------------------------------------------------------ the token budget
def test_the_agent_stops_when_its_daily_token_budget_is_used(client, auth, ci, session, model):
    reviewed(client, auth, ci, session, daily_limit=50)
    used = client.get("/api/automations", headers=auth).json()["controls"]["tokens_used"]
    assert used > 0  # the two reviews cost what the model reported
    controls(client, auth, token_budget=used)
    ci["send_pr"](number=7, sha="b" * 16)
    run = session.query(AgentRun).order_by(AgentRun.created_at.desc()).first()
    assert run.status == "skipped" and "daily token budget" in run.reason
    c = controls(client, auth, token_budget=0)  # no limit
    assert c["token_budget"] == 0
    assert len(ci["send_pr"](number=7, sha="c" * 16)["review"]) == 1


def test_the_page_shows_what_each_run_used(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (r,) = runs(client, auth)
    assert r["tokens"] > 0


def test_only_an_admin_sets_the_controls_and_a_bad_budget_is_refused(client, auth, ci):
    member = make_user(client, auth, "dev@acme.io")
    assert client.put("/api/automations/controls", headers=member, json={"paused": True}).status_code == 403
    assert client.put("/api/automations/controls", headers=auth, json={"token_budget": -1}).status_code == 422
    assert controls(client, auth, paused=False, token_budget=5000)["token_budget"] == 5000


# ------------------------------------------------------------------------------------------------ cancel and the trace
def test_a_run_that_is_going_on_can_be_cancelled_and_its_drafts_step_aside(client, auth, ci, session):
    turn_on(client, auth)
    ci["send"]()
    (r,) = runs(client, auth)
    assert r["can_cancel"] is True and r["status"] == "started"
    assert client.post(f"/api/automations/runs/{r['id']}/cancel", headers=auth).status_code == 200
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.status == "cancelled" and run.reason == "Cancelled by an admin"
    assert {d.status for d in session.query(AgentDraft).all()} == {"dismissed"}
    assert session.query(AuditEvent).filter_by(action="automation.cancel").count() == 1
    (again,) = runs(client, auth)
    assert again["can_cancel"] is False and again["retry"] is None
    assert client.post(f"/api/automations/runs/{r['id']}/cancel", headers=auth).status_code == 409


def test_only_an_admin_cancels_and_only_in_their_workspace(client, auth, ci):
    turn_on(client, auth)
    ci["send"]()
    (r,) = runs(client, auth)
    member = make_user(client, auth, "dev@acme.io")
    assert client.post(f"/api/automations/runs/{r['id']}/cancel", headers=member).status_code == 403
    from tests.conftest import register

    assert (
        client.post(
            f"/api/automations/runs/{r['id']}/cancel", headers=register(client, "x@other.io", "Other")
        ).status_code
        == 404
    )


def test_work_that_is_cancelled_while_it_goes_on_stops_at_its_next_step(client, auth, ci, session, model, monkeypatch):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)

    def cancelled_now(db, run):
        run.status = "cancelled"
        db.commit()
        return True

    monkeypatch.setattr(agent_controls, "cancelled", cancelled_now)
    ci["send_pr"]()
    session.expire_all()
    assert session.query(AgentRun).one().status == "cancelled" and session.query(PersonaRun).count() == 0
    assert session.query(AgentDraft).count() == 0


def test_a_run_says_why_it_started_and_what_it_did(client, auth, ci, session):
    turn_on(client, auth)
    ci["send"]()
    (r,) = runs(client, auth)
    said = " | ".join(t["what"] for t in r["trace"])
    assert "workflow run 501 failed on main" in said and "rule for every repository is on" in said
    assert "1 failed job(s)" in said and "started: Started 1 investigation(s)" in said
    assert all(t["at"] for t in r["trace"])


# ------------------------------------------------------------------------------------------------ retries when GitHub is down
def flaky(monkeypatch, ci, failures, status=None):
    """The jobs listing fails `failures` times (a dropped connection, or an HTTP status), then works."""
    real = gh_api._transport
    left = {"n": failures}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/actions/runs/501/jobs") and left["n"] > 0:
            left["n"] -= 1
            if status:
                return httpx.Response(status, json={"message": "no"})
            raise httpx.ConnectError("connection dropped")
        return real.handle_request(request)

    monkeypatch.setattr(gh_api, "_transport", httpx.MockTransport(handler))
    return left


def test_a_run_is_tried_again_when_github_cannot_be_reached(client, auth, ci, session, monkeypatch):
    monkeypatch.setenv("REPOLENS_AUTOMATION_RETRY_DELAYS", "[0, 0]")
    get_settings.cache_clear()
    flaky(monkeypatch, ci, 2)
    turn_on(client, auth)
    ci["send"]()
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.status == "started" and run.detail["attempts"] == 3 and len(run.investigation_ids) == 1
    said = " | ".join(t["what"] for t in run.detail["trace"])
    assert "GitHub could not be reached" in said and "try 2 of 3" in said and "try 3 of 3" in said


def test_a_run_gives_up_after_its_tries_and_says_why(client, auth, ci, session, monkeypatch):
    monkeypatch.setenv("REPOLENS_AUTOMATION_RETRY_DELAYS", "[0]")
    get_settings.cache_clear()
    flaky(monkeypatch, ci, 5)
    turn_on(client, auth)
    ci["send"]()
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.status == "failed" and "GitHub said no" in run.reason and run.detail["attempts"] == 2


def test_a_refusal_is_not_tried_again(client, auth, ci, session, monkeypatch):
    monkeypatch.setenv("REPOLENS_AUTOMATION_RETRY_DELAYS", "[0, 0]")
    get_settings.cache_clear()
    left = flaky(monkeypatch, ci, 5, status=404)
    turn_on(client, auth)
    ci["send"]()
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.status == "failed" and "attempts" not in run.detail and left["n"] == 4  # asked once, not again


def test_a_rate_limit_is_tried_again(client, auth, ci, session, monkeypatch):
    monkeypatch.setenv("REPOLENS_AUTOMATION_RETRY_DELAYS", "[0]")
    get_settings.cache_clear()
    flaky(monkeypatch, ci, 1, status=502)
    turn_on(client, auth)
    ci["send"]()
    session.expire_all()
    assert session.query(AgentRun).one().status == "started"


# ------------------------------------------------------------------------------------------------ health
def test_a_run_that_never_finished_is_marked_failed_and_the_health_page_says_so(client, auth, ci, session):
    turn_on(client, auth)
    ci["send"]()
    run = session.query(AgentRun).one()
    run.status, run.created_at = "started", datetime.now(UTC) - timedelta(hours=2)
    session.commit()
    h = client.get("/api/metrics/health", headers=auth).json()
    assert h["agent"]["stuck"] == 1 and any("going on for over 45 minutes" in p for p in h["problems"])
    assert agent_controls.reap_stuck(session) == 1
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.status == "failed" and "did not finish" in run.reason
    h = client.get("/api/metrics/health", headers=auth).json()
    assert (
        h["agent"]["failed_24h"] == 1 and any("failed in the last day" in p for p in h["problems"]) and h["ok"] is False
    )


def test_a_quiet_agent_adds_no_problem_to_the_health_page(client, auth, ci):
    h = client.get("/api/metrics/health", headers=auth).json()
    assert h["agent"] == {
        "paused": False,
        "token_budget": 0,
        "tokens_used": 0,
        "failed_24h": 0,
        "stuck": 0,
        "problems": [],
    }


# ------------------------------------------------------------------------------------------------ a failed run on a pull request
def failed_on_pr(client, ci):
    return post_event(
        client,
        "workflow_run",
        {
            "action": "completed",
            "workflow_run": {
                "id": 501,
                "name": "CI",
                "conclusion": "failure",
                "head_branch": "feature/x",
                "run_attempt": 1,
                "head_sha": "d" * 40,
                "html_url": "https://github.com/acme/shop/actions/runs/501",
                "pull_requests": [{"number": 7}],
            },
            "repository": {"clone_url": CLONE, "full_name": "acme/shop"},
        },
    )


def test_a_failed_run_on_a_pull_request_leaves_a_comment_waiting_for_its_investigation(client, auth, ci, session):
    turn_on(client, auth, all_branches=True)
    failed_on_pr(client, ci)
    drafts = {d["kind"]: d for d in inbox(client, auth)}
    c = drafts["pr_comment"]
    assert c["pending"] is True and c["body"] == "" and "pull request #7" in c["title"]
    r = client.post(f"/api/automations/inbox/{c['id']}/approve", headers=auth, json={})
    assert r.status_code == 400 and "still running" in r.json()["detail"]
    assert not ci.get("posted")


def finish(session, status="completed"):
    inv = session.query(Investigation).one()
    inv.status, inv.root_cause, inv.confidence = status, "The null guard was removed in the profile lookup", 0.9
    inv.suggested_fix, inv.evidence = "Restore the guard", {"llm_used": False}
    session.commit()
    agent_triggers.on_investigation_done(session, inv)
    session.expire_all()


def test_when_the_investigation_finishes_the_comment_is_filled_in_and_can_be_posted(client, auth, ci, session):
    turn_on(client, auth, all_branches=True)
    failed_on_pr(client, ci)
    finish(session)
    (c,) = [d for d in inbox(client, auth) if d["kind"] == "pr_comment"]
    assert c["pending"] is False and "### RepoLens looked at the failed CI run" in c["body"]
    assert (
        "Likely cause** (90% confidence): The null guard was removed" in c["body"]
        and "Suggested fix:** Restore the guard" in c["body"]
    )
    assert "Built from evidence by code" in c["body"]  # no model wrote it, and it says so
    assert client.post(f"/api/automations/inbox/{c['id']}/approve", headers=auth, json={}).status_code == 200
    assert ci["posted"][0]["body"].startswith("### RepoLens looked at the failed CI run")


def test_if_the_investigation_fails_there_is_no_comment(client, auth, ci, session):
    turn_on(client, auth, all_branches=True)
    failed_on_pr(client, ci)
    finish(session, "failed")
    assert [d for d in inbox(client, auth) if d["kind"] == "pr_comment"] == []
    assert session.query(AgentDraft).filter_by(kind="pr_comment").one().status == "dismissed"


def test_a_failed_run_that_is_not_on_a_pull_request_makes_no_comment(client, auth, ci, session):
    turn_on(client, auth)
    ci["send"]()
    assert [d["kind"] for d in inbox(client, auth)] == ["investigation"]


# ------------------------------------------------------------------------------------------------ a stale page gets a drafted update
def turn_on_event(client, auth, event, **body):
    r = client.put("/api/automations/rules/all", headers=auth, json={"enabled": True, "event": event, **body})
    assert r.status_code == 200, r.text


def make_drift(client, auth, path, rule_first=True):
    """The drift scenario of the wiki tests, with the agent's rule switched on before the index that finds the drift."""
    from tests.test_api import add_repo
    from tests.test_drift import reindex
    from tests.test_knowledge import make_collection, upload
    from tests.test_risk import ORDERS, commit

    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(
        client,
        auth,
        col["id"],
        {"billing.md": "# Billing\n\nTotals come from `compute_total`.\n\n## Tax\nAdded last.\n"},
    )
    if rule_first:
        turn_on_event(client, auth, "drift_draft")
    commit(
        path,
        {"shop/orders.py": ORDERS.replace("(1 + tax_rate)", "(1 + tax_rate) + 1")},
        "fix: add handling fee to totals",
        "2026-09-01T10:00:00Z",
    )
    reindex(client, auth, repo["id"])
    return repo


def billing_alert(session):
    return session.query(DriftAlert).filter_by(doc_path="billing.md").one()


def test_a_page_that_describes_changed_code_gets_a_drafted_update_after_an_index(client, auth, shop, session):  # noqa: F811
    make_drift(client, auth, shop[0])
    session.expire_all()
    alert = billing_alert(session)
    run = session.query(AgentRun).filter_by(event="drift_draft", event_key=alert.id).one()
    assert run.status == "started" and "billing.md" in run.reason
    p = session.query(DocProposal).filter_by(source_ref=alert.id).one()
    assert p.source == "drift" and p.status == "pending"  # it waits for a person, like any proposal
    drafts = [x for x in inbox(client, auth) if x["kind"] == "wiki_proposal" and "billing.md" in x["body"]]
    assert len(drafts) == 1
    assert session.query(AgentDraft).filter_by(run_id=run.id).one().meta["proposal_id"] == p.id
    assert session.query(AuditEvent).filter_by(action="automation.drift_draft").count() >= 1


def test_the_same_stale_page_is_drafted_for_once(client, auth, shop, session):  # noqa: F811
    from tests.test_drift import reindex

    repo = make_drift(client, auth, shop[0])
    before = (session.query(AgentRun).filter_by(event="drift_draft").count(), session.query(DocProposal).count())
    reindex(client, auth, repo["id"])
    session.expire_all()
    assert (
        session.query(AgentRun).filter_by(event="drift_draft").count(),
        session.query(DocProposal).count(),
    ) == before


def test_nothing_is_drafted_until_the_rule_is_on(client, auth, shop, session):  # noqa: F811
    make_drift(client, auth, shop[0], rule_first=False)
    assert session.query(AgentRun).count() == 0 and session.query(DocProposal).count() == 0


def test_a_paused_agent_drafts_no_page(client, auth, shop, session):  # noqa: F811
    controls(client, auth, paused=True)
    make_drift(client, auth, shop[0])
    run = session.query(AgentRun).filter_by(event="drift_draft", event_key=billing_alert(session).id).one()
    assert run.status == "skipped" and "paused" in run.reason and session.query(DocProposal).count() == 0


# ------------------------------------------------------------------------------------------------ the nightly watch
def test_the_nightly_watch_lists_pages_that_have_been_out_of_date_for_long_and_only_once_a_week(
    client, auth, shop, session
):  # noqa: F811
    make_drift(client, auth, shop[0], rule_first=False)
    turn_on_event(client, auth, "watch")
    assert agent_triggers.nightly(session) == 0  # the alert is new: not stale yet
    for a in session.query(DriftAlert).all():
        a.created_at = datetime.now(UTC) - timedelta(days=30)
    session.commit()
    assert agent_triggers.nightly(session) == 1
    (d,) = [x for x in inbox(client, auth) if x["kind"] == "watch"]
    assert "billing.md" in d["body"] and "out of date for over 14 days" in d["title"]
    assert agent_triggers.nightly(session) == 0  # once a week for each repository
    assert session.query(AuditEvent).filter_by(action="automation.watch").count() == 1
    assert session.query(AgentRun).filter_by(event="watch").one().status == "started"


def test_the_nightly_watch_is_off_until_a_rule_turns_it_on(client, auth, shop, session):  # noqa: F811
    make_drift(client, auth, shop[0], rule_first=False)
    for a in session.query(DriftAlert).all():
        a.created_at = datetime.now(UTC) - timedelta(days=30)
    session.commit()
    assert agent_triggers.nightly(session) == 0


def test_the_four_events_each_have_their_own_rules(client, auth, ci):
    e = client.get("/api/automations", headers=auth).json()["events"]
    assert set(e) == {"ci_failure", "pr_review", "drift_draft", "watch"}
    assert (
        client.put("/api/automations/rules/all", headers=auth, json={"enabled": True, "event": "nightly"}).status_code
        == 422
    )
