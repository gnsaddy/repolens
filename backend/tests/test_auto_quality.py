"""The saved questions check themselves once an index has changed what answers are built from."""

import subprocess

import pytest

from repolens import config
from repolens.models import GoldenRun, Repository
from repolens.services import golden as svc
from tests.test_api import add_repo
from tests.test_golden import add


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("REPOLENS_AUTO_QUALITY_CHECKS", "true")
    config.get_settings.cache_clear()


@pytest.fixture
def repo(client, auth, demo_repo):
    return add_repo(client, auth, demo_repo[0])


def runs(session):
    session.expire_all()
    return session.query(GoldenRun).order_by(GoldenRun.started_at).all()


def reindex(client, auth, repo):
    assert client.post(f"/api/repositories/{repo['id']}/index", headers=auth).status_code == 202


def commit(path, name):
    (path / f"src/{name}.py").write_text(f"def {name}():\n    return 1\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name], check=True
    )


def test_an_index_that_finishes_runs_the_saved_questions_and_says_so(client, auth, repo, session):
    add(client, auth, expect_files=["user_profile.py"])
    reindex(client, auth, repo)
    (r,) = runs(session)
    assert r.trigger == "reindex" and r.mode == "retrieval" and r.status == "completed" and r.passed == 1
    shown = client.get("/api/golden", headers=auth).json()
    assert shown["latest"]["trigger"] == "reindex"  # the page can say it ran by itself


def test_nothing_runs_when_the_workspace_has_no_saved_questions(client, auth, repo, session):
    reindex(client, auth, repo)
    assert runs(session) == []


def test_an_index_that_found_nothing_new_does_not_run_them_again(client, auth, repo, session):
    add(client, auth, expect_files=["user_profile.py"])
    reindex(client, auth, repo)
    reindex(client, auth, repo)
    assert len(runs(session)) == 1


def test_new_code_runs_them_again_and_a_lost_answer_is_reported(client, auth, repo, demo_repo, session, caplog):
    add(client, auth, expect_files=["user_profile.py"])
    reindex(client, auth, repo)
    path, _ = demo_repo
    commit(path, "extra")
    reindex(client, auth, repo)
    assert [r.trigger for r in runs(session)] == ["reindex", "reindex"]
    shown = client.get("/api/golden", headers=auth).json()
    assert shown["compare"]["delta"] == 0 and shown["compare"]["broke"] == []

    add(client, auth, "How does get_profile treat a deleted user?", expect_files=["not_in_this_repo.py"])
    commit(path, "more")
    with caplog.at_level("INFO", logger="repolens.golden"):
        reindex(client, auth, repo)
    assert len(runs(session)) == 3
    assert any("quality check after re-index" in m for m in caplog.messages)


def test_it_waits_while_another_repository_is_still_indexing(client, auth, repo, session):
    add(client, auth, expect_files=["user_profile.py"])
    org = session.get(Repository, repo["id"]).org_id
    session.get(Repository, repo["id"]).status = "indexing"
    session.commit()
    assert svc.auto_run(org) is False and runs(session) == []
    session.get(Repository, repo["id"]).status = "ready"
    session.commit()
    assert svc.auto_run(org) is True


def test_several_repositories_finishing_together_push_one_check_back_not_one_each(monkeypatch):
    monkeypatch.setenv("REPOLENS_RUN_JOBS_INLINE", "false")
    config.get_settings.cache_clear()
    made = []

    class Spy:
        def __init__(self, wait, fn, args=()):
            self.wait, self.cancelled, self.daemon = wait, False, False
            made.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    svc.after_index("org-1", delay=30, timer=Spy)
    svc.after_index("org-1", delay=30, timer=Spy)
    svc.after_index("org-2", delay=30, timer=Spy)
    assert [t.cancelled for t in made] == [True, False, False]  # org-1's first check was replaced, org-2's was not
    svc._waiting.clear()


def test_it_can_be_switched_off(client, auth, repo, session, monkeypatch):
    monkeypatch.setenv("REPOLENS_AUTO_QUALITY_CHECKS", "false")
    config.get_settings.cache_clear()
    add(client, auth, expect_files=["user_profile.py"])
    reindex(client, auth, repo)
    assert runs(session) == []
