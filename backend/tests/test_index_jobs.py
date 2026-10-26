import inspect
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from repolens import config, main
from repolens.api import repos as repos_api
from repolens.models import IndexRun, Repository
from repolens.services import index_jobs as jobs
from tests.test_api import add_repo


class FakeTimer:
    made: list["FakeTimer"] = []

    def __init__(self, wait, fn, args=()):
        self.wait, self.fn, self.args, self.daemon, self.started = wait, fn, args, False, False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True


@pytest.fixture(autouse=True)
def clean():
    jobs._tries.clear()
    jobs._waiting.clear()
    FakeTimer.made.clear()


def git(path, *cmd):
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True)


def make_repo(client, auth, tmp_path, name="svc"):
    d = tmp_path / name
    d.mkdir()
    (d / "a.py").write_text("def a():\n    return 1\n")
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    return d, add_repo(client, auth, str(d), name=name)


def test_a_timeout_is_worth_trying_again_and_a_wrong_path_or_password_is_not():
    assert jobs.retryable("git log timed out")
    assert jobs.retryable("fatal: unable to access 'https://x/': Could not resolve host")
    assert not jobs.retryable("/srv/x is not a git repository")
    assert not jobs.retryable("remote: Invalid username or password. Authentication failed for 'https://x/'")
    assert not jobs.retryable("branch 'dev' not found in the repository")
    assert not jobs.retryable("fatal: repository 'https://x/y.git/' not found")


def test_a_failed_index_is_tried_again_after_each_wait_and_then_left_alone():
    sched = []
    waits = [jobs.after_run("r1", "failed", "git log timed out", sched.append, [60, 300], FakeTimer) for _ in range(3)]
    assert waits[0] == 60 and waits[1] is None  # the first retry is still on its way, so no second one is stacked on it
    jobs._waiting.clear()  # the first retry has run
    assert jobs.after_run("r1", "failed", "git log timed out", sched.append, [60, 300], FakeTimer) == 300
    jobs._waiting.clear()
    assert jobs.after_run("r1", "failed", "git log timed out", sched.append, [60, 300], FakeTimer) is None  # gave up
    assert [t.wait for t in FakeTimer.made] == [60, 300] and all(t.started and t.daemon for t in FakeTimer.made)


def test_a_success_resets_the_count_and_a_permanent_error_is_not_retried():
    jobs.after_run("r2", "failed", "git log timed out", lambda _: None, [60, 300], FakeTimer)
    jobs._waiting.clear()
    jobs.after_run("r2", "completed", None, lambda _: None, [60, 300], FakeTimer)
    assert "r2" not in jobs._tries
    assert jobs.after_run("r3", "failed", "x is not a git repository", lambda _: None, [60], FakeTimer) is None
    assert jobs.after_run("r4", "failed", "git log timed out", lambda _: None, [], FakeTimer) is None  # retries off
    assert len(FakeTimer.made) == 1


def test_the_retry_only_runs_if_the_repository_is_still_in_error(client, auth, session, tmp_path):
    _, r = make_repo(client, auth, tmp_path)
    ran = []
    jobs._again(r["id"], ran.append)  # it indexed fine, so nothing to retry
    assert ran == []
    repo = session.get(Repository, r["id"])
    repo.status = "error"
    session.commit()
    jobs._again(r["id"], ran.append)
    assert ran == [r["id"]]


def test_a_finished_index_hands_its_result_to_the_retry_logic(client, auth, session, tmp_path, monkeypatch):
    _, r = make_repo(client, auth, tmp_path)
    monkeypatch.setenv("REPOLENS_RUN_JOBS_INLINE", "false")
    config.get_settings.cache_clear()
    monkeypatch.setattr(
        repos_api, "index_repository", lambda db, rid: SimpleNamespace(status="failed", error="git log timed out")
    )
    seen = []
    monkeypatch.setattr(jobs, "after_run", lambda *a: seen.append(a))
    repos_api._run_index(r["id"])
    assert seen and seen[0][:3] == (r["id"], "failed", "git log timed out") and seen[0][4] == [60.0, 300.0]


def test_a_repository_is_indexed_again_only_when_its_code_moved_on(client, auth, session, tmp_path):
    d, r = make_repo(client, auth, tmp_path)
    repo = session.get(Repository, r["id"])
    assert repo.status == "ready" and repo.indexed_sha
    assert jobs.changed(session, repo) is False  # nothing new
    (d / "b.py").write_text("def b():\n    return 2\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "more")
    assert jobs.changed(session, repo) is True
    repo.indexed_sha = None
    assert jobs.changed(session, repo) is True  # never finished


def test_failed_stuck_and_running_repositories_are_told_apart(client, auth, session, tmp_path):
    _, r = make_repo(client, auth, tmp_path)
    repo = session.get(Repository, r["id"])
    repo.status = "error"
    assert jobs.changed(session, repo) is True  # the last index failed: try again
    repo.status, repo.updated_at = "indexing", datetime.now(UTC)
    assert jobs.changed(session, repo) is False  # running right now
    repo.updated_at = datetime.now(UTC) - timedelta(hours=4)
    assert jobs.changed(session, repo) is True  # the server stopped in the middle long ago
    repo.status, repo.source = "ready", "docs"
    assert jobs.changed(session, repo) is False  # a document collection has no code to check


def test_the_daily_pass_queues_only_what_changed(client, auth, session, tmp_path):
    d1, r1 = make_repo(client, auth, tmp_path, "one")
    _, r2 = make_repo(client, auth, tmp_path, "two")
    (d1 / "c.py").write_text("x = 1\n")
    git(d1, "add", "-A")
    git(d1, "commit", "-qm", "change")
    queued: list[str] = []
    names = jobs.daily_pass(session, queued.append)
    assert names == ["one"] and queued == [r1["id"]] and r2["id"] not in queued


def test_the_repository_list_says_why_the_last_index_failed(client, auth, session, tmp_path):
    _, ok = make_repo(client, auth, tmp_path, "fine")
    _, bad = make_repo(client, auth, tmp_path, "broken")
    repo = session.get(Repository, bad["id"])
    repo.status = "error"
    session.add(IndexRun(repo_id=bad["id"], status="failed", error="git log timed out"))
    session.commit()
    listing = {x["name"]: x for x in client.get("/api/repositories", headers=auth).json()}
    assert listing["broken"]["last_error"] == "git log timed out" and listing["fine"]["last_error"] is None
    assert client.get(f"/api/repositories/{bad['id']}", headers=auth).json()["last_error"] == "git log timed out"
    assert client.get(f"/api/repositories/{ok['id']}", headers=auth).json()["last_error"] is None


def test_the_background_jobs_are_actually_started():
    """The retention sweeper was written but never started; keep every background job in the start-up list."""
    src = inspect.getsource(main.lifespan)
    for job in ("_source_scheduler", "_investigation_reaper", "_retention_sweeper", "_auto_reindexer"):
        assert job in src, job


def test_a_remotes_latest_commit_can_be_read_without_cloning(tmp_path, monkeypatch):
    from repolens.services import gitops

    monkeypatch.setattr(gitops, "_SCHEMES", (*gitops._SCHEMES, "file://"))
    d = tmp_path / "remote"
    d.mkdir()
    (d / "a.txt").write_text("1")
    git(d, "init", "-q", "-b", "main")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "one")
    first = gitops.head_sha(str(d))
    assert gitops.remote_head(f"file://{d}") == first == gitops.remote_head(f"file://{d}", "main")
    (d / "a.txt").write_text("2")
    git(d, "commit", "-aqm", "two")
    assert gitops.remote_head(f"file://{d}") != first  # moved on, which is what the daily check looks for
    assert gitops.remote_head(f"file://{d}", "no-such-branch") == ""
    with pytest.raises(gitops.GitError):
        gitops.remote_head("ftp://example.com/x")
