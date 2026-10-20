import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repolens.models import CodeChunk, Organization, Repository
from repolens.services import checkouts, gitops
from repolens.services.indexer import index_repository
from tests.conftest import register


def sh(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture
def origin(tmp_path):
    """A repository with `main` and a `feature` branch that adds a file."""
    p = tmp_path / "origin"
    p.mkdir()
    sh(p, "git", "init", "-q", "-b", "main")
    sh(p, "git", "config", "uploadpack.allowFilter", "true")
    sh(p, "git", "config", "uploadpack.allowAnySHA1InWant", "true")
    (p / "app.py").write_text("def main_only():\n    return 1\n")
    sh(p, "git", "add", "."), sh(p, "git", "commit", "-q", "-m", "init")
    sh(p, "git", "checkout", "-q", "-b", "feature")
    (p / "extra.py").write_text("def feature_only():\n    return 2\n")
    sh(p, "git", "add", "."), sh(p, "git", "commit", "-q", "-m", "feature work")
    sh(p, "git", "checkout", "-q", "main")
    return p


@pytest.fixture
def allow_file(monkeypatch):
    monkeypatch.setattr(gitops, "_SCHEMES", ("https://", "ssh://", "git@", "file://"))


def git_repo(session, url, name, branch, shared):
    org = session.query(Organization).first() or Organization(name="o")
    session.add(org)
    session.flush()
    r = Repository(org_id=org.id, name=name, source="git", url=url, branch=branch, local_path=shared)
    session.add(r)
    session.commit()
    return r


def symbols(session, repo):
    return {c.symbol_name for c in session.query(CodeChunk).filter_by(repo_id=repo.id)}


def test_branch_listing_and_validation(allow_file, origin):
    names, default = gitops.list_remote_branches(f"file://{origin}")
    assert names == ["feature", "main"] and default == "main"
    assert gitops.list_local_branches(str(origin)) == (["feature", "main"], "main")
    for bad in ("-x", "a..b", "x.lock", "a b", "", "a/"):
        assert not gitops.valid_branch(bad)
    assert gitops.valid_branch("feature/login-2")
    with pytest.raises(gitops.GitError):
        gitops.list_remote_branches("file:///nope")


def test_partial_clone_has_no_worktree_and_prefetches_in_one_go(allow_file, origin, tmp_path, session):
    dest = tmp_path / "clone"
    gitops.clone(f"file://{origin}", str(dest), branch="main", filter_spec="blob:none")
    assert gitops.is_partial(str(dest))
    assert not (dest / "app.py").exists()  # no working tree
    n = gitops.prefetch_blobs(str(dest), "refs/remotes/origin/main", lambda f: f.endswith(".py"))
    assert n == 1
    assert gitops.prefetch_blobs(str(dest), "refs/remotes/origin/main", lambda f: True) == 0  # nothing left
    assert "main_only" in gitops.read_file(str(dest), "refs/remotes/origin/main", "app.py").decode()


def test_each_branch_is_indexed_separately_from_one_shared_clone(allow_file, origin, tmp_path, session):
    shared = str(tmp_path / "clones" / "shared")
    main = git_repo(session, f"file://{origin}", "proj", "main", shared)
    feat = git_repo(session, f"file://{origin}", "proj@feature", "feature", shared)
    index_repository(session, main.id)
    index_repository(session, feat.id)
    session.refresh(main), session.refresh(feat)
    assert main.status == feat.status == "ready", (main.status, feat.status)
    assert main.default_branch == "main" and feat.default_branch == "feature"
    assert "main_only" in symbols(session, main) and "feature_only" not in symbols(session, main)
    assert "feature_only" in symbols(session, feat)
    assert main.indexed_sha != feat.indexed_sha
    assert gitops.is_partial(shared) and not (Path(shared) / "app.py").exists()


def test_unknown_branch_fails_with_a_clear_error(allow_file, origin, tmp_path, session):
    r = git_repo(session, f"file://{origin}", "proj", "nope", str(tmp_path / "clones" / "x"))
    run = index_repository(session, r.id)
    session.refresh(r)
    assert r.status == "error" and run.error


def test_cleanup_removes_only_stale_managed_clones_and_reclones_on_demand(allow_file, origin, tmp_path, session):
    shared = str(tmp_path / "clones" / "shared")
    r = git_repo(session, f"file://{origin}", "proj", "main", shared)
    index_repository(session, r.id)
    org_id = r.org_id
    assert checkouts.usage(session, org_id)[0]["bytes"] > 0
    assert checkouts.cleanup(session, org_id, 30)["removed"] == []  # used just now
    r.last_used_at = datetime.now(UTC) - timedelta(days=45)
    session.commit()
    dry = checkouts.cleanup(session, org_id, 30, dry_run=True)
    assert len(dry["removed"]) == 1 and Path(shared).exists()
    res = checkouts.cleanup(session, org_id, 30)
    assert res["freed_bytes"] > 0 and not Path(shared).exists()
    run = index_repository(session, r.id)  # clone is restored transparently
    assert run.status == "completed" and Path(shared).exists()


def test_cleanup_never_touches_indexing_or_unmanaged_paths(allow_file, origin, tmp_path, session):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "f").write_text("x")
    r = git_repo(session, f"file://{origin}", "proj", "main", str(outside))
    r.last_used_at = datetime.now(UTC) - timedelta(days=999)
    session.commit()
    assert checkouts.cleanup(session, r.org_id, 1)["removed"] == [] and (outside / "f").exists()
    managed = tmp_path / "clones" / "m"
    managed.mkdir(parents=True)
    r.local_path, r.status = str(managed), "indexing"
    session.commit()
    assert checkouts.cleanup(session, r.org_id, 1)["removed"] == [] and managed.exists()


def test_api_branches_create_many_and_storage(client, auth, origin):
    r = client.get("/api/repositories/branches", headers=auth, params={"source": "local", "url": str(origin)})
    assert r.status_code == 200 and r.json() == {"branches": ["feature", "main"], "default": "main"}
    assert (
        client.get("/api/repositories/branches", headers=auth, params={"source": "git", "url": "file:///x"}).status_code
        == 400
    )
    assert (
        client.get(
            "/api/repositories/branches", headers=auth, params={"source": "git", "url": "https://u:p@x.io/a/b"}
        ).status_code
        == 400
    )
    made = client.post(
        "/api/repositories",
        headers=auth,
        json={"name": "proj", "source": "local", "url": str(origin), "branches": ["main", "feature"]},
    )
    assert made.status_code == 201, made.text
    repos = {x["name"]: x for x in client.get("/api/repositories", headers=auth).json()}
    assert set(repos) == {"proj", "proj@feature"}
    assert repos["proj"]["branch"] == "main" and repos["proj@feature"]["branch"] == "feature"
    assert repos["proj"]["status"] == repos["proj@feature"]["status"] == "ready"
    files = lambda rid: {f["path"] for f in client.get(f"/api/repositories/{rid}/tree", headers=auth).json()}  # noqa: E731
    assert "extra.py" in files(repos["proj@feature"]["id"]) and "extra.py" not in files(repos["proj"]["id"])
    dup = client.post(
        "/api/repositories",
        headers=auth,
        json={"name": "proj", "source": "local", "url": str(origin), "branches": ["main"]},
    )
    assert dup.status_code == 409
    assert (
        client.post(
            "/api/repositories",
            headers=auth,
            json={"name": "q", "source": "local", "url": str(origin), "branches": ["--bad"]},
        ).status_code
        == 400
    )
    assert (
        client.get("/api/repositories/storage", headers=auth).json()["checkouts"] == []
    )  # local repos have no managed clone
    cl = client.post("/api/repositories/storage/cleanup", headers=auth, json={"older_than_days": 1})
    assert cl.status_code == 200 and cl.json()["removed"] == []
    member = register(client, "m@acme.io", "Other")
    assert client.post("/api/repositories/storage/cleanup", headers=member, json={}).status_code in (200, 403)
