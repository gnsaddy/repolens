"""Agent personas: a specialist reviews code, and only findings that quote real code are kept."""

import json

import pytest

from repolens.config import get_settings
from repolens.models import PersonaRun
from repolens.services import llm
from repolens.services import personas as svc
from repolens.services.llm import LLMResult
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_groups import days_ago
from tests.test_risk import commit, git

AUTH = (
    "import sqlite3\n\n\n"
    "def find_user(db, name):\n"
    '    cur = db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")\n'
    "    return cur.fetchone()\n\n\n"
    "def total(items, discount=0):\n    return sum(items) - discount\n"
)
REQUEST = "the user lookup in auth and how find_user builds its database query"

SECURITY = {
    "summary": "find_user builds SQL by joining the name into the text.",
    "findings": [
        {
            "title": "SQL injection in find_user",
            "severity": "high",
            "file": "app/auth.py",
            "evidence": 'cur = db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")',
            "explanation": "name comes from the caller and is joined into the query.",
            "suggestion": "Pass the name as a parameter.",
            "code": "cur = db.execute('SELECT * FROM users WHERE name = ?', (name,))",
        },
        {
            "title": "Invented problem",
            "severity": "high",
            "file": "app/auth.py",
            "evidence": "password = request.args['password']  # stored in plain text",
            "explanation": "not in the code",
            "suggestion": "",
        },
        {"title": "Wrong file", "severity": "medium", "file": "app/other.py", "evidence": "x = 1", "explanation": ""},
    ],
}
TESTS = {
    "summary": "total has no test for a discount larger than the sum.",
    "findings": [
        {
            "title": "total with a discount over the sum",
            "severity": "medium",
            "file": "app/auth.py",
            "evidence": "return sum(items) - discount",
            "explanation": "The result goes negative.",
            "suggestion": "Assert what should happen.",
            "code": "def test_total_never_negative():\n    assert total([1], 5) >= 0\n",
        },
        {
            "title": "broken sample",
            "severity": "low",
            "file": "app/auth.py",
            "evidence": "def total(items, discount=0):",
            "explanation": "x",
            "suggestion": "y",
            "code": "def test_(:\n",
        },
    ],
}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    state = type("M", (), {"reply": SECURITY, "calls": 0, "fail": None, "systems": []})()

    def fake(system, prompt, max_tokens, cfg=None):
        state.calls += 1
        state.systems.append(system)
        assert "untrusted" in system and "FILE app/auth.py" in prompt
        if state.fail:
            raise RuntimeError(state.fail)
        return LLMResult(json.dumps(state.reply), 1500, 400)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    yield state
    get_settings.cache_clear()


@pytest.fixture
def repo(client, auth, tmp_path):
    p = tmp_path / "shop"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(p, {"app/auth.py": AUTH, "README.md": "# shop\n"}, "feat: shop", days_ago(3))
    return add_repo(client, auth, p, name="shop")


def run(client, h, repo, persona="security-auditor", request=REQUEST, **extra):
    body = {"repo_id": repo["id"], "persona": persona, "request": request, **extra}
    return client.post("/api/personas/runs", headers=h, json=body)


def test_nothing_about_personas_is_available_without_signing_in(client):
    for method, url in (("get", "/api/personas"), ("get", "/api/personas/runs"), ("post", "/api/personas/runs")):
        assert getattr(client, method)(url).status_code == 401


def test_the_personas_are_listed_with_what_each_one_does(client, auth):
    got = client.get("/api/personas", headers=auth).json()["personas"]
    assert [p["key"] for p in got] == ["security-auditor", "test-engineer"]
    assert all(p["summary"] and p["placeholder"] for p in got)


def test_a_finding_is_kept_only_when_it_quotes_code_that_is_in_the_file(client, auth, repo, model):
    r = run(client, auth, repo)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "done" and d["persona_name"] == "Security auditor" and model.calls == 1
    (f,) = d["findings"]
    assert f["title"] == "SQL injection in find_user" and f["severity"] == "high" and f["file"] == "app/auth.py"
    assert f["line"] == 5 and "SELECT * FROM users" in f["evidence"]  # the line comes from the code, not the model
    assert d["notes"]["dropped"] == 2 and len(d["notes"]["reasons"]) == 2
    assert any("not in app/auth.py" in why for why in d["notes"]["reasons"])
    assert any("not shown" in why for why in d["notes"]["reasons"])
    assert d["notes"]["coverage"] == [{"file": "app/auth.py", "whole": True}] and "not run" in d["notes"]["limit"]
    assert d["tokens"] == {"calls": 1, "input_tokens": 1500, "output_tokens": 400, "cost": d["tokens"]["cost"]}
    assert "security reviewer" in model.systems[0]


def test_the_test_engineer_is_another_stance_and_suggested_test_code_is_checked_for_syntax(client, auth, repo, model):
    model.reply = TESTS
    d = run(client, auth, repo, "test-engineer").json()
    assert "test engineer" in model.systems[0] and [f["code_ok"] for f in d["findings"]] == [True, False]
    assert d["findings"][0]["severity"] == "medium" and d["persona_name"] == "Test engineer"


def test_an_unknown_persona_a_short_request_and_no_model_are_refused_plainly(client, auth, repo, model, monkeypatch):
    assert run(client, auth, repo, "poet").status_code == 409
    assert (
        client.post(
            "/api/personas/runs", headers=auth, json={"repo_id": repo["id"], "persona": "x", "request": "no"}
        ).status_code
        == 422
    )
    assert model.calls == 0
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "heuristic")
    get_settings.cache_clear()
    r = run(client, auth, repo)
    get_settings.cache_clear()
    assert r.status_code == 409 and "needs a language model" in r.json()["detail"]


def test_a_failed_model_call_is_reported_and_a_request_with_no_matching_code_is_refused(
    client, auth, repo, model, session
):
    model.fail = "overloaded"
    r = run(client, auth, repo)
    assert r.status_code == 409 and "could not write the review" in r.json()["detail"]
    assert session.query(PersonaRun).one().status == "failed"
    model.fail, model.calls = None, 0
    r = run(client, auth, repo, request="zebra giraffe telescope orchestration for the moon base")
    assert r.status_code == 409 and model.calls == 0


def test_the_same_review_in_other_words_is_reused_but_another_persona_or_fresh_is_not(client, auth, repo, model):
    first = run(client, auth, repo).json()
    again = run(
        client, auth, repo, request="how find_user builds its database query, in the user lookup of auth"
    ).json()
    assert again["id"] == first["id"] and again["reused"] and model.calls == 1
    model.reply = TESTS
    other = run(client, auth, repo, "test-engineer").json()
    assert other["id"] != first["id"] and not other["reused"] and model.calls == 2
    fresh = run(client, auth, repo, fresh=True).json()
    assert fresh["id"] != first["id"] and model.calls == 3


def test_a_review_is_private_to_its_author_until_shared_and_only_the_author_changes_it(client, auth, repo, model):
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    assert run(client, viewer, repo).status_code == 403
    mine = run(client, ann, repo).json()
    url = f"/api/personas/runs/{mine['id']}"
    for other in (bob, auth):
        assert client.get(url, headers=other).status_code == 404
        assert client.get("/api/personas/runs", headers=other).json()["runs"] == []
    assert client.patch(url, headers=ann, json={"shared": True}).json()["shared"] is True
    for reader in (bob, viewer, auth):
        seen = client.get(url, headers=reader).json()
        assert seen["id"] == mine["id"] and seen["mine"] is False and seen["author_name"] == "ann"
        assert [r["id"] for r in client.get("/api/personas/runs", headers=reader).json()["runs"]] == [mine["id"]]
    assert client.patch(url, headers=bob, json={"shared": False}).status_code == 403
    assert client.delete(url, headers=bob).status_code == 403
    assert client.patch(url, headers=ann, json={"shared": False}).json()["shared"] is False
    assert client.get(url, headers=bob).status_code == 404
    assert client.delete(url, headers=ann).status_code == 204


def test_a_shared_review_of_a_repository_someone_cannot_open_is_not_found(client, auth, repo, model):
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    made = run(client, ann, repo).json()
    client.patch(f"/api/personas/runs/{made['id']}", headers=ann, json={"shared": True})
    allowed = [client.get("/api/auth/me", headers=ann).json()["id"]]
    client.put(
        f"/api/repositories/{repo['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": allowed}
    )
    assert client.get(f"/api/personas/runs/{made['id']}", headers=bob).status_code == 404
    assert client.get("/api/personas/runs", headers=bob).json()["runs"] == []


def test_deleting_a_repository_removes_its_reviews(client, auth, repo, model, session):
    run(client, auth, repo)
    assert client.delete(f"/api/repositories/{repo['id']}", headers=auth).status_code in (200, 204)
    session.expire_all()
    assert session.query(PersonaRun).count() == 0


def test_verify_forgives_whitespace_reads_several_lines_and_ranks_by_severity():
    text = "def a():\n    x = 1\n    return x\n\n\ndef b():\n    y = 2\n    return y\n"
    texts = {"m.py": text}

    def f(title, evidence, sev="low", file="m.py"):
        return {"title": title, "severity": sev, "file": file, "evidence": evidence}

    kept, why = svc.verify(
        [
            f("one", "return   y", "low"),
            f("two", "def b():\n  y = 2", "high"),
            f("none", ""),
            f("odd", "return x", "bogus"),
        ],
        texts,
    )
    assert [(k["title"], k["line"], k["severity"]) for k in kept] == [
        ("two", 6, "high"),
        ("one", 8, "low"),
        ("odd", 3, "info"),
    ]
    assert len(why) == 1 and "none: the quoted code is not in m.py" in why[0]
    assert len(svc.verify([f(str(i), "return x") for i in range(40)], texts)[0]) == svc.MAX_FINDINGS
    assert svc.verify("not a list", texts) == ([], [])
