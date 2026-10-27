"""RepoLens watches its own housekeeping: background loops, indexes, answers and the saved-question checks."""

from prometheus_client import REGISTRY

from repolens import telemetry
from tests.test_api import add_repo
from tests.test_chat import make_user


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_a_loop_that_never_ran_shows_as_late_not_as_missing():
    telemetry._jobs.clear()
    telemetry.register_job("retention", every_s=100, first_s=10)
    start = telemetry._jobs["retention"].started
    assert telemetry.job_report(now=start + 5)[0]["state"] == "waiting"
    assert telemetry.job_report(now=start + 10 + 2 * 100 + 61)[0]["state"] == "late"


def test_a_loop_is_ok_after_a_pass_failing_after_a_raise_and_late_when_it_stops():
    telemetry._jobs.clear()
    telemetry.register_job("source-sync", every_s=60)
    telemetry.job_ok("source-sync")
    last = telemetry._jobs["source-sync"].last_ok
    assert telemetry.job_report(now=last + 10)[0]["state"] == "ok"
    telemetry.job_failed("source-sync", RuntimeError("database is down\nmore detail"))
    one = telemetry.job_report(now=last + 10)[0]
    assert one["state"] == "failing" and one["last_error"] == "database is down" and one["failures"] == 1
    telemetry.job_ok("source-sync")  # the next pass worked
    assert telemetry.job_report()[0]["state"] == "ok"
    assert telemetry.job_report(now=telemetry._jobs["source-sync"].last_ok + 2 * 60 + 61)[0]["state"] == "late"


def test_indexing_and_answering_are_counted(client, auth, demo_repo):
    before = sample("repolens_index_runs_total", status="completed")
    add_repo(client, auth, demo_repo[0])
    assert sample("repolens_index_runs_total", status="completed") == before + 1
    r = client.post("/api/knowledge/ask", headers=auth, json={"query": "Where is get_profile defined?"})
    assert r.status_code == 200, r.text
    mode = r.json()["mode"]
    assert sample("repolens_answers_total", mode=mode, cached="no") >= 1
    assert sample("repolens_answer_seconds_count", mode=mode) >= 1


def test_the_metrics_page_counts_repositories_by_status(client, auth, demo_repo):
    add_repo(client, auth, demo_repo[0])
    text = client.get("/metrics").text
    assert 'repolens_repositories{status="ready"} 1.0' in text
    assert "repolens_index_seconds_count" in text and "repolens_job_failures_total" in text


def test_health_is_for_admins_and_lists_the_loops_the_indexes_and_the_last_check(client, auth, demo_repo):
    telemetry._jobs.clear()
    telemetry.register_job("retention", every_s=3600)
    telemetry.job_ok("retention")
    add_repo(client, auth, demo_repo[0])
    h = client.get("/api/metrics/health", headers=auth).json()
    assert h["ok"] is True and h["problems"] == []
    assert h["jobs"][0]["name"] == "retention" and h["jobs"][0]["state"] == "ok"
    assert h["repositories"]["ready"] == 1 and h["quality"] is None

    telemetry.job_failed("retention", RuntimeError("boom"))
    bad = client.get("/api/metrics/health", headers=auth).json()
    assert bad["ok"] is False and "The retention loop is failing" in bad["problems"][0]

    member = make_user(client, auth, "dev@acme.io", "member")
    assert client.get("/api/metrics/health", headers=member).status_code == 403
