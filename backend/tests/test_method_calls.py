"""A call on an object is no evidence of a dependency on a same-named function in another repository."""

from repolens.models import KGEdge, KGNode
from repolens.services.graph import method_calls_only
from tests.test_api import add_repo
from tests.test_risk import commit, git


def test_calls_on_an_object_are_told_from_plain_calls():
    assert method_calls_only("rows = conn.execute(sql)", "execute")
    assert method_calls_only("return self._db.execute(q)\n", "execute")
    assert method_calls_only("Type::make(x); obj->make(y)", "make")
    assert not method_calls_only("execute(sql)", "execute")
    assert not method_calls_only("x = db.execute(sql)\nexecute(other)", "execute")
    assert not method_calls_only("no call here", "execute")


def test_a_method_call_does_not_link_to_a_same_named_function_in_another_repository(client, auth, session, tmp_path):
    lib = tmp_path / "toolkit"
    lib.mkdir()
    git(lib, "init", "-q", "-b", "main")
    commit(lib, {"toolkit/run.py": "def execute(job):\n    return job()\n"}, "feat: toolkit", "2026-02-01T10:00:00Z")
    app = tmp_path / "webapp"
    app.mkdir()
    git(app, "init", "-q", "-b", "main")
    files = {
        "web/store.py": "def load(conn):\n    return conn.execute('select 1')\n",
        "web/jobs.py": "def go(job):\n    return execute(job)\n",
    }
    commit(app, files, "feat: web", "2026-02-02T10:00:00Z")
    add_repo(client, auth, lib, name="toolkit")
    web = add_repo(client, auth, app, name="webapp")
    session.expire_all()
    names = {n.id: n.name for n in session.query(KGNode).filter(KGNode.repo_id == web["id"])}
    calls = [e.src_id for e in session.query(KGEdge).filter(KGEdge.rel == "CALLS") if e.src_id in names]
    assert [names[i] for i in calls] == ["go"]  # the plain call links; conn.execute(...) in load() does not
