"""Automations: a failed CI run starts investigations by itself, only when an admin turned it on."""

import hashlib
import hmac
import json

import httpx
import pytest

from repolens.api import github as gh_api
from repolens.config import get_settings
from repolens.models import AgentRun, AuditEvent, Investigation, PersonaRun, Repository
from repolens.services import automations as svc
from tests.test_chat import make_user
from tests.test_github import TOKEN, connect
from tests.test_personas import AUTH, SECURITY, model  # noqa: F401  (fixture: a fake model)

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
    state = {
        "log": PYTEST_LOG,
        "jobs": [{"id": 11, "name": "tests", "conclusion": "failure"}],
        "requests": [],
        "pr_files": [
            {
                "filename": "app/auth.py",
                "status": "modified",
                "additions": 3,
                "deletions": 1,
                "patch": "@@ -1,3 +1,6 @@\n+import sqlite3\n",
            },
            {"filename": "logo.png", "status": "added", "additions": 0, "deletions": 0},
            {"filename": "old.py", "status": "removed", "additions": 0, "deletions": 9},
            {
                "filename": "backend/Dockerfile",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1,2 +1,2 @@\n-RUN apt-get update\n+RUN apt-get update && apt-get upgrade -y\n",
            },
            {"filename": ".env", "status": "added", "additions": 2, "deletions": 0},
            {"filename": "package-lock.json", "status": "modified", "additions": 300, "deletions": 20},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.method == "POST" and request.url.path == "/repos/acme/shop/issues/7/comments":
            if state.get("comment_status"):
                return httpx.Response(state["comment_status"], json={"message": "Resource not accessible"})
            state.setdefault("posted", []).append(json.loads(request.content))
            return httpx.Response(201, json={"html_url": "https://github.com/acme/shop/pull/7#issuecomment-1"})
        p = request.url.path
        if request.url.host == "logs.example.net":
            return httpx.Response(200, content=state["log"].encode())
        if p == "/user":
            return httpx.Response(200, json={"login": "bot"})
        if p == "/repos/acme/shop/pulls/7":
            return httpx.Response(
                200, json={"title": "Fix the user lookup", "head": {"sha": "abc123def4567890"}, "base": {"ref": "main"}}
            )
        if p == "/repos/acme/shop/pulls/7/files":
            return httpx.Response(200, json=state["pr_files"])
        if p == "/repos/acme/shop/contents/app/auth.py":
            return httpx.Response(200, content=AUTH.encode())
        if p == "/repos/acme/shop/contents/backend/Dockerfile":
            return httpx.Response(200, content=b"FROM python:3.12-slim\nRUN apt-get update && apt-get upgrade -y\n")
        if p == "/repos/acme/shop/contents/logo.png":
            return httpx.Response(200, content=b"\x89PNG\x00\x00binary")
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

    def send_pr(number=7, action="opened", draft=False, base="main", sha="abc123def4567890"):
        body = json.dumps(
            {
                "action": action,
                "pull_request": {
                    "number": number,
                    "title": "Fix the user lookup",
                    "draft": draft,
                    "head": {"sha": sha},
                    "base": {"ref": base},
                    "html_url": f"https://github.com/acme/shop/pull/{number}",
                },
                "repository": {"clone_url": CLONE, "full_name": "acme/shop"},
            }
        ).encode()
        sig = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
        hdr = {"X-GitHub-Event": "pull_request", "Content-Type": "application/json", "X-Hub-Signature-256": sig}
        r = client.post("/api/webhooks/github", content=body, headers=hdr)
        assert r.status_code == 200, r.text
        return r.json()

    state["send"] = send
    state["send_pr"] = send_pr
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


def test_a_log_with_no_test_failure_is_read_where_github_marks_the_error_not_at_its_end(client, auth, ci, session):
    scan = "\n".join(f"2026-11-10T16:26:2{i % 10}.0000000Z scanning layer {i}" for i in range(80))
    ci["log"] = (
        "2026-11-10T16:26:01.0000000Z Run trivy image repolens-api:ci\n" + scan + "\n"
        "2026-11-10T16:26:22.0000000Z zlib1g CVE-2026-76844 HIGH fixed in 1.3.1-1+deb13u1\n"
        "2026-11-10T16:26:22.5000000Z ##[error]Process completed with exit code 1.\n"
        + "\n".join(f"2026-11-10T16:26:3{i % 10}.0000000Z Uploading artifact part {i}" for i in range(200))
        + "\nFinalizing artifact upload\n"
    )
    turn_on(client, auth)
    ci["send"]()
    q = session.query(Investigation).one().query
    assert "CVE-2026-76844" in q and "Process completed with exit code 1" in q
    assert (
        "Uploading artifact part" not in q and "2026-11-10T16" not in q
    )  # the steps after the failure, and the time stamps, are left out


def test_the_excerpt_falls_back_to_the_first_line_about_a_failure_and_then_to_the_end():
    plain = "start\n" + "ok\n" * 100 + "fatal: could not read from remote\n" + "after\n" * 100
    assert "fatal: could not read from remote" in svc.failure_excerpt(plain)
    assert svc.failure_excerpt("a\nb\nc").splitlines() == ["a", "b", "c"]  # nothing says failure: the end of the log
    assert len(svc.failure_excerpt("##[error]" + "x" * 20000)) == svc.TAIL


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
    assert seen["events"]["ci_failure"]["repositories"] == [] and seen["runs"] == []


def test_the_overview_lists_repositories_rules_and_recent_runs(client, auth, ci):
    turn_on(client, auth, ci["repo"]["id"], daily_limit=3)
    ci["send"]()
    o = client.get("/api/automations", headers=auth).json()
    row = o["events"]["ci_failure"]["repositories"][0]
    assert row["name"] == "shop" and row["enabled"] is True and row["daily_limit"] == 3 and row["own_rule"] is True
    assert o["events"]["ci_failure"]["all"]["enabled"] is False and o["events"]["ci_failure"]["used_today"] == 1
    assert o["events"]["pr_review"]["repositories"][0]["enabled"] is False  # each event has its own rules
    assert (
        o["runs"][0]["status"] == "started" and o["runs"][0]["repo"] == "shop" and o["runs"][0]["event"] == "ci_failure"
    )


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


# ------------------------------------------------------------------------------------------------ which model, and is it AI
def test_the_overview_says_there_is_no_model_when_none_is_connected(client, auth, ci):
    m = client.get("/api/automations", headers=auth).json()["model"]
    assert m["ai"] is False and all(r["model"] is None for r in m["routing"])


def test_the_overview_names_the_model_when_one_is_connected(client, auth, ci, model):
    m = client.get("/api/automations", headers=auth).json()["model"]
    assert m["ai"] is True and m["provider"] == "anthropic" and m["model"]
    tasks = {r["task"]: r for r in m["routing"]}
    assert set(tasks) == {"Investigation reports", "Pull request reviews"}
    assert tasks["Pull request reviews"]["tier"] == "standard"  # the one model is on one tier, which every task uses
    assert tasks["Pull request reviews"]["model"] == m["model"] == tasks["Investigation reports"]["model"]
    assert m["multi"] is False  # one model: it does everything


def test_an_investigation_started_without_a_model_is_marked_as_not_ai_written(client, auth, ci, session, monkeypatch):
    monkeypatch.setattr("repolens.services.automations.submit", lambda i: None)
    turn_on(client, auth)
    ci["send"]()
    run = client.get("/api/automations", headers=auth).json()["runs"][0]
    inv = run["investigations"][0]
    assert inv["ai"] is False and inv["models"] == [] and inv["title"].startswith("CI failure:")


# ------------------------------------------------------------------------------------------------ pull request review
def index_it(session, repo_id):
    session.query(Repository).filter_by(id=repo_id).one().indexed_sha = "abc123"
    session.commit()


def turn_on_pr(client, auth, repo_id="all", **body):
    r = client.put(
        f"/api/automations/rules/{repo_id}", headers=auth, json={"enabled": True, "event": "pr_review", **body}
    )
    assert r.status_code == 200, r.text


def test_a_pull_request_is_not_reviewed_until_an_admin_turns_it_on(client, auth, ci, session, model):
    assert ci["send_pr"]()["review"] == []
    assert session.query(AgentRun).count() == 0 and session.query(PersonaRun).count() == 0
    turn_on(client, auth)  # the CI rule does not turn on pull request reviews
    assert ci["send_pr"](number=8)["review"] == []


def test_a_new_pull_request_is_reviewed_by_two_specialists_from_its_own_files(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    assert len(ci["send_pr"]()["review"]) == 1
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.event == "pr_review" and run.status == "started" and "2 review(s) of 2 file(s)" in run.reason
    assert run.detail["pull_request"] == 7 and run.detail["files"] == [
        "app/auth.py",
        "backend/Dockerfile",
    ]  # a Dockerfile is read; the binary and the removed file are not
    assert [r["persona"] for r in run.detail["reviews"]] == ["developer", "security-auditor"]
    rows = session.query(PersonaRun).all()
    assert len(rows) == 2 and all(r.status == "done" and r.model and r.created_by for r in rows)
    assert all("Review pull request #7" in r.request and "abc123d" in r.request for r in rows)
    assert all(r.findings and r.findings[0]["title"] == "SQL injection in find_user" for r in rows)
    ev = session.query(AuditEvent).filter_by(action="automation.pr_review").one()
    assert ev.target == "shop" and ev.detail["pull_request"] == 7 and ev.detail["reviews"] == 2
    shown = client.get("/api/automations", headers=auth).json()["runs"][0]
    assert shown["reviews"][0]["ai"] is True and shown["reviews"][0]["model"] and shown["reviews"][0]["findings"] == 1


def test_nothing_is_posted_to_github_by_a_review(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"]()
    assert [r.method for r in ci["requests"] if r.url.host == "api.github.com"] == ["GET"] * len(
        [r for r in ci["requests"] if r.url.host == "api.github.com"]
    )


def test_without_a_model_the_review_says_so_instead_of_making_one_up(client, auth, ci, session):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"]()
    run = session.query(AgentRun).one()
    assert run.status == "skipped" and "No language model" in run.reason and session.query(PersonaRun).count() == 0


def test_a_draft_a_new_commit_or_a_closed_pull_request_is_not_reviewed(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    assert ci["send_pr"](draft=True)["review"] == "ignored"
    assert ci["send_pr"](action="synchronize")["review"] == []  # a new commit is only reviewed when the rule says so
    assert ci["send_pr"](action="closed")["review"] == "ignored"
    assert session.query(AgentRun).count() == 0
    assert len(ci["send_pr"](action="ready_for_review")["review"]) == 1  # taken out of draft: now it is


def test_the_same_pull_request_at_the_same_commit_is_reviewed_once(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"]()
    assert ci["send_pr"](action="reopened")["review"] == []
    assert session.query(AgentRun).count() == 1


def test_only_pull_requests_into_the_repositorys_branch_unless_all_branches_is_on(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"](base="release")
    run = session.query(AgentRun).one()
    assert run.status == "skipped" and "Not a pull request into main" in run.reason


def test_the_daily_limit_counts_reviewed_pull_requests(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth, daily_limit=1)
    ci["send_pr"]()
    ci["send_pr"](number=7, sha="ffffffffffff0000")
    session.expire_all()
    last = session.query(AgentRun).order_by(AgentRun.created_at.desc()).first()
    assert last.status == "skipped" and "limit of 1 reviewed pull requests" in last.reason
    assert svc.used_today(session, session.query(AgentRun).first().org_id, "pr_review") == 1


def test_the_two_events_have_separate_switches_and_limits(client, auth, ci):
    turn_on_pr(client, auth, ci["repo"]["id"], daily_limit=2)
    e = client.get("/api/automations", headers=auth).json()["events"]
    assert (
        e["pr_review"]["repositories"][0]["enabled"] is True and e["pr_review"]["repositories"][0]["daily_limit"] == 2
    )
    assert e["ci_failure"]["repositories"][0]["enabled"] is False
    assert (
        client.put("/api/automations/rules/all", headers=auth, json={"enabled": True, "event": "deploy"}).status_code
        == 422
    )


def test_the_changed_line_ranges_come_from_the_diff():
    patch = "@@ -1,3 +1,6 @@\n a\n@@ -20 +23,2 @@\n b\n@@ -40,2 +45,0 @@\n c\n"
    assert svc.hunks(patch) == [(1, 6), (23, 24)]  # a hunk that only deletes lines has nothing in the new file
    assert svc.hunks("") == []


# ------------------------------------------------------------------------------------------------ the inbox
def reviewed(client, auth, ci, session, **rule):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth, **rule)
    ci["send_pr"]()


def inbox(client, auth, status="open"):
    return client.get(f"/api/automations/inbox?status={status}", headers=auth).json()["drafts"]


def test_a_reviewed_pull_request_leaves_a_draft_comment_in_the_inbox(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    assert d["kind"] == "pr_comment" and d["status"] == "new" and d["repo"] == "shop" and d["posted_url"] == ""
    assert "### RepoLens review of #7 at `abc123d`" in d["body"] and "AI generated by" in d["body"]
    assert "#### Developer" in d["body"] and "#### Security auditor" in d["body"]
    assert "SQL injection in find_user" in d["body"] and "`app/auth.py:5`" in d["body"]
    assert {r["persona"] for r in d["reviews"]} == {"developer", "security-auditor"} and d["files"] == [
        "app/auth.py",
        "backend/Dockerfile",
    ]


def test_a_failed_ci_run_leaves_a_pointer_to_its_investigations_in_the_inbox(client, auth, ci, session, monkeypatch):
    monkeypatch.setattr("repolens.services.automations.submit", lambda i: None)
    turn_on(client, auth)
    ci["send"]()
    (d,) = inbox(client, auth)
    assert (
        d["kind"] == "investigation" and d["body"] == "" and d["investigations"][0]["title"].startswith("CI failure:")
    )
    assert (
        client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={}).status_code == 400
    )  # nothing to post


def test_nothing_is_written_to_github_until_an_admin_approves(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    assert not ci.get("posted") and not [
        r for r in ci["requests"] if r.method != "GET" and r.url.host == "api.github.com"
    ]


def test_approving_posts_the_comment_with_a_footer_and_records_who_and_where(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    r = client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={})
    assert r.status_code == 200 and r.json() == {
        "id": d["id"],
        "status": "posted",
        "posted_url": "https://github.com/acme/shop/pull/7#issuecomment-1",
    }
    (sent,) = ci["posted"]
    assert sent["body"].startswith("### RepoLens review of #7") and "posted after a person approved it" in sent["body"]
    ev = session.query(AuditEvent).filter_by(action="automation.comment_posted").one()
    assert ev.target == "acme/shop" and ev.detail["pull_request"] == 7 and ev.actor_id
    assert inbox(client, auth) == [] and inbox(client, auth, "all")[0]["status"] == "posted"
    assert client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={}).status_code == 409
    assert (
        client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "dismissed"}).status_code == 409
    )


def test_an_admin_can_edit_the_comment_before_it_is_posted(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    client.post(
        f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={"body": "Only the first point matters."}
    )
    assert ci["posted"][0]["body"].startswith("Only the first point matters.")
    assert "posted after a person approved it" in ci["posted"][0]["body"]
    assert (
        client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={"body": "x" * 70000}).status_code
        == 422
    )


def test_a_refusal_from_github_keeps_the_draft_and_says_why(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    ci["comment_status"] = 403
    (d,) = inbox(client, auth)
    r = client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={})
    assert r.status_code == 502 and "write access" in r.json()["detail"]
    (again,) = inbox(client, auth)
    assert again["status"] == "new" and "write access" in again["error"] and again["posted_url"] == ""
    ci["comment_status"] = None
    assert client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={}).status_code == 200


def test_a_draft_can_be_marked_read_or_dismissed(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    assert client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "read"}).status_code == 200
    assert inbox(client, auth)[0]["status"] == "read"
    # "dismissed" is the old name for archive
    client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "dismissed"})
    assert inbox(client, auth) == [] and inbox(client, auth, "all")[0]["status"] == "archived"
    assert client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "posted"}).status_code == 422


def test_only_admins_see_or_act_on_the_inbox_and_only_in_their_workspace(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    member = make_user(client, auth, "dev@acme.io")
    assert client.get("/api/automations/inbox", headers=member).status_code == 403
    assert client.post(f"/api/automations/inbox/{d['id']}/approve", headers=member, json={}).status_code == 403
    assert client.patch(f"/api/automations/inbox/{d['id']}", headers=member, json={"status": "read"}).status_code == 403
    from tests.conftest import register

    stranger = register(client, "x@other.io", "Other")
    assert client.get("/api/automations/inbox", headers=stranger).json()["drafts"] == []
    assert client.post(f"/api/automations/inbox/{d['id']}/approve", headers=stranger, json={}).status_code == 404
    assert not ci.get("posted")


# ------------------------------------------------------------------------------------------------ review again after a new commit
def test_a_new_commit_is_not_reviewed_unless_the_rule_says_so(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    assert ci["send_pr"](action="synchronize", sha="1111111111112222")["review"] == []
    assert session.query(AgentRun).count() == 1


def test_a_new_commit_is_reviewed_again_when_the_rule_says_so_up_to_a_cap(client, auth, ci, session, model):
    reviewed(client, auth, ci, session, daily_limit=50, re_review=True)
    for n in range(1, 5):
        assert len(ci["send_pr"](action="synchronize", sha=f"{n}" * 16)["review"]) == 1
    last = ci["send_pr"](action="synchronize", sha="9" * 16)
    assert last["review"] == []  # five reviews of this pull request is the cap; the sixth is recorded as skipped
    run = session.query(AgentRun).order_by(AgentRun.created_at.desc()).first()
    assert run.status == "skipped" and "Already reviewed 5 times" in run.reason
    assert client.get("/api/automations", headers=auth).json()["events"]["pr_review"]["all"]["re_review"] is True


def test_review_again_is_a_pull_request_option_only(client, auth, ci):
    turn_on(client, auth, re_review=True)  # a CI rule has no such option
    assert client.get("/api/automations", headers=auth).json()["events"]["ci_failure"]["all"]["re_review"] is False


# ------------------------------------------------------------------------------------------------ try again, review again
def test_a_model_that_answers_with_something_unusable_is_tried_once_more(client, auth, ci, session, model, monkeypatch):
    from repolens.services import personas

    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    real, calls = personas.run, []

    def flaky(*a, **kw):
        calls.append(a[5])
        if len(calls) == 1:
            raise personas.PersonaError("The AI model could not write the review. Expecting value (model: x)")
        return real(*a, **kw)

    monkeypatch.setattr(personas, "run", flaky)
    ci["send_pr"]()
    run = session.query(AgentRun).one()
    assert calls == ["developer", "developer", "security-auditor"]  # the second try of the developer worked
    assert [r["status"] for r in run.detail["reviews"]] == ["done", "done"]
    assert run.detail["reviews"][0]["attempts"] == 2 and run.detail["reviews"][1]["attempts"] == 1


def test_a_failure_that_is_not_the_models_is_not_retried(client, auth, ci, session, model, monkeypatch):
    from repolens.services import personas

    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    calls = []

    def refuse(*a, **kw):
        calls.append(a[5])
        raise personas.PersonaError("This repository has no index yet.")

    monkeypatch.setattr(personas, "run", refuse)
    ci["send_pr"]()
    assert calls == ["developer", "security-auditor"]
    run = session.query(AgentRun).one()
    assert run.status == "failed" and "no index yet" in run.detail["reviews"][0]["summary"]


def test_a_failed_run_says_how_to_try_again_and_trying_again_works(client, auth, ci, session):
    from repolens.models import GithubConnection

    turn_on(client, auth)
    saved = session.query(GithubConnection).one()
    session.delete(saved)
    session.commit()
    ci["send"]()
    (r,) = client.get("/api/automations", headers=auth).json()["runs"]
    assert r["status"] == "failed" and r["retry"] == "try_again"
    connect(client, auth)  # the connection is back
    out = client.post(f"/api/automations/runs/{r['id']}/retry", headers=auth)
    assert out.status_code == 200
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.status == "started" and len(run.investigation_ids) == 1 and session.query(Investigation).count() == 1
    assert session.query(AuditEvent).filter_by(action="automation.retry").count() == 1
    assert (
        client.get("/api/automations", headers=auth).json()["runs"][0]["retry"] is None
    )  # a CI run that started has nothing to retry


def test_a_skipped_run_can_be_run_anyway(client, auth, ci, session):
    turn_on(client, auth)
    ci["send"](branch="feature/x")
    (r,) = client.get("/api/automations", headers=auth).json()["runs"]
    assert r["status"] == "skipped" and r["retry"] == "run_anyway"
    client.post(f"/api/automations/runs/{r['id']}/retry", headers=auth)
    session.expire_all()
    assert session.query(AgentRun).one().status == "started" and session.query(Investigation).count() == 1


def test_a_reviewed_pull_request_can_be_reviewed_again_and_the_old_draft_steps_aside(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (r,) = client.get("/api/automations", headers=auth).json()["runs"]
    assert r["retry"] == "review_again"
    (old,) = inbox(client, auth)
    assert client.post(f"/api/automations/runs/{r['id']}/retry", headers=auth).status_code == 200
    (new,) = inbox(client, auth)
    assert new["id"] != old["id"] and new["status"] == "new"
    assert [d["id"] for d in inbox(client, auth, "all")] == [new["id"]]  # the old draft is gone, not kept
    assert session.query(PersonaRun).count() == 4 and session.query(AgentRun).count() == 1


def test_only_an_admin_can_try_a_run_again_and_only_in_their_workspace(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (r,) = client.get("/api/automations", headers=auth).json()["runs"]
    member = make_user(client, auth, "dev@acme.io")
    assert client.post(f"/api/automations/runs/{r['id']}/retry", headers=member).status_code == 403
    from tests.conftest import register

    assert (
        client.post(
            f"/api/automations/runs/{r['id']}/retry", headers=register(client, "x@other.io", "Other")
        ).status_code
        == 404
    )
    assert client.post("/api/automations/runs/nope/retry", headers=auth).status_code == 404


# ------------------------------------------------------------------------------------------------ what a review reads
def test_a_dockerfile_or_a_workflow_is_read_but_a_secret_file_and_a_lock_file_are_not(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"]()
    run = session.query(AgentRun).one()
    assert (
        "backend/Dockerfile" in run.detail["files"]
    )  # what a reviewer should read: not on the list a fix may not edit
    skipped = {s["file"]: s["why"] for s in run.detail["skipped"]}
    assert skipped[".env"] == "it may hold a secret" and skipped["package-lock.json"] == "it is a lock file"
    assert "logo.png" in skipped and "binary" in skipped["logo.png"]
    asked = [
        r
        for r in ci["requests"]
        if r.url.path.endswith("/contents/.env") or r.url.path.endswith("/contents/package-lock.json")
    ]
    assert asked == []  # a secret file is never even fetched
    said = " | ".join(t["what"] for t in run.detail["trace"])
    assert "Not read: .env (it may hold a secret)" in said


def test_the_reviewers_are_told_which_changed_files_they_were_not_shown(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"]()
    request = session.query(PersonaRun).first().request
    assert "Changed but NOT shown to you" in request and ".env (it may hold a secret)" in request
    assert "do not say the pull request changes only what you were shown" in request


def test_the_page_lists_what_a_review_did_not_read(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"]()
    (r,) = client.get("/api/automations", headers=auth).json()["runs"]
    assert {s["file"] for s in r["skipped"]} == {".env", "package-lock.json", "logo.png"}


def test_which_files_a_review_skips(client):
    assert svc.skip_reason(".env") and svc.skip_reason("deploy/.env.production") and svc.skip_reason("keys/server.pem")
    assert svc.skip_reason("yarn.lock") and svc.skip_reason("web/package-lock.json") and svc.skip_reason("go.sum")
    for ok in (
        "backend/Dockerfile",
        ".github/workflows/ci.yml",
        "docker-compose.yml",
        "pyproject.toml",
        "package.json",
        "deploy/k8s/app.yaml",
        "src/env.py",
    ):
        assert svc.skip_reason(ok) is None, ok


# ------------------------------------------------------------------------------------------------ posted and archived
def tab(client, auth, name):
    return client.get(f"/api/automations/inbox?status={name}", headers=auth).json()


def test_a_posted_comment_is_listed_under_posted_with_its_text_link_and_who_posted_it(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={"body": "The first point matters."})
    posted = tab(client, auth, "posted")
    assert posted["counts"] == {"open": 0, "posted": 1, "archived": 0}
    (p,) = posted["drafts"]
    assert p["id"] == d["id"] and p["status"] == "posted" and p["body"].startswith("The first point matters.")
    assert "posted after a person approved it" in p["body"]  # the text exactly as it went on GitHub
    assert (
        p["posted_url"] == "https://github.com/acme/shop/pull/7#issuecomment-1" and p["decided_by"] and p["decided_at"]
    )
    assert tab(client, auth, "open")["drafts"] == []


def test_a_posted_comment_can_be_archived_and_restored_and_stays_on_github(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={})
    assert (
        client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "archived"}).status_code == 200
    )
    a = tab(client, auth, "archived")
    assert a["counts"] == {"open": 0, "posted": 0, "archived": 1} and a["drafts"][0]["posted_url"].endswith(
        "issuecomment-1"
    )
    assert tab(client, auth, "posted")["drafts"] == []
    assert (
        client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "archived"}).status_code == 409
    )
    assert (
        client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "restore"}).status_code == 200
    )
    assert tab(client, auth, "posted")["counts"]["posted"] == 1  # back under Posted, still with its address
    assert len(ci["posted"]) == 1  # nothing was posted again, and nothing was deleted on GitHub


def test_a_draft_that_was_never_posted_is_archived_and_restored_to_waiting(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "archived"})
    assert tab(client, auth, "open")["drafts"] == [] and tab(client, auth, "archived")["drafts"][0]["id"] == d["id"]
    client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "restore"})
    assert tab(client, auth, "open")["drafts"][0]["status"] == "new"
    assert (
        client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": "restore"}).status_code == 409
    )  # not archived


def test_what_the_agent_replaces_is_deleted_and_not_archived(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (r,) = client.get("/api/automations", headers=auth).json()["runs"]
    client.post(f"/api/automations/runs/{r['id']}/retry", headers=auth)  # the old draft steps aside
    assert tab(client, auth, "archived")["counts"]["archived"] == 0 and tab(client, auth, "open")["counts"]["open"] == 1


def test_a_posted_comment_cannot_be_marked_read_or_dismissed(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    client.post(f"/api/automations/inbox/{d['id']}/approve", headers=auth, json={})
    for status in ("read", "new", "dismissed", "restore"):
        assert (
            client.patch(f"/api/automations/inbox/{d['id']}", headers=auth, json={"status": status}).status_code == 409
        )


def test_only_an_admin_archives_and_only_in_their_workspace(client, auth, ci, session, model):
    reviewed(client, auth, ci, session)
    (d,) = inbox(client, auth)
    member = make_user(client, auth, "dev@acme.io")
    assert (
        client.patch(f"/api/automations/inbox/{d['id']}", headers=member, json={"status": "archived"}).status_code
        == 403
    )
    from tests.conftest import register

    assert (
        client.patch(
            f"/api/automations/inbox/{d['id']}",
            headers=register(client, "x@other.io", "Other"),
            json={"status": "archived"},
        ).status_code
        == 404
    )
