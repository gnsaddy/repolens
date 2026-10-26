import subprocess

import pytest

from repolens.services import walkthrough as wt
from repolens.services.llm import LLMClient, LLMResult
from tests.test_api import add_repo
from tests.test_chat import new_chat, say

API = """from fastapi import APIRouter
router = APIRouter(prefix="/api/auth")


@router.post("/users", status_code=201)
def create_user(body, p):
    authorize(p, "user.create")
    role = body.role
    save_user(body.email, role)
    return {"role": role}


@router.get("/users")
def list_users(p):
    return []


@router.get("/health")
def health():
    return "ok"
"""
POLICY = '''def authorize(p, action):
    """Only admins may create users and assign roles."""
    if p.role != "admin":
        raise PermissionError(action)


def save_user(email, role):
    db.add(email, role)
'''


@pytest.fixture
def svc(client, auth, tmp_path):
    d = tmp_path / "svc"
    d.mkdir()
    (d / "auth.py").write_text(API)
    (d / "policy.py").write_text(POLICY)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(d), *cmd], check=True)
    return add_repo(client, auth, str(d), name="svc")


@pytest.mark.parametrize(
    "q,expected",
    [
        ("Check the user endpoint, how user will assign the roles?", True),
        ("How does sign-in work?", True),
        ("Why does the retry back off?", True),
        ("Explain the login flow", True),
        ("Walk me through a deploy", True),
        ("How many times does the payment service retry?", False),
        ("How often is the cache cleared", False),
        ("Where is the retry policy documented?", False),
        ("What depends on the payment service?", False),
    ],
)
def test_which_questions_get_a_walkthrough(q, expected):
    assert wt.is_explain(q) is expected


def test_the_route_its_handler_and_the_permission_check_are_found(client, session, svc):
    from repolens.models import Organization

    org = session.query(Organization).first().id
    got = wt.route_sources(session, org, "Check the user endpoint, how user will assign the roles?", None, None)
    titles = [r["title"] for r in got]
    assert titles[0] == "POST /api/auth/users"  # the route that mentions roles in its handler beats list/get
    assert any(t.startswith("create_user (handler for POST /api/auth/users)") for t in titles)
    assert any(t.startswith("authorize (called by create_user)") for t in titles)
    assert not any("health" in t for t in titles)
    assert got[0]["start_line"] == 5 and "create_user()" in got[0]["content"] and got[0]["file"] == "auth.py"


def test_nothing_is_added_when_the_question_names_no_topic(client, session, svc):
    from repolens.models import Organization

    org = session.query(Organization).first().id
    assert wt.route_sources(session, org, "How does it work?", None, None) == []


class Spy:
    def __init__(self):
        self.calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))

        def complete(this, agent, system, prompt, max_tokens=1500, task=None, validate=None):
            self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
            return LLMResult("Summary. [S1]", 10, 10, route={"model": "spy"})

        monkeypatch.setattr(LLMClient, "complete", complete)
        return self


def test_chat_uses_the_walkthrough_prompt_and_the_route_sources_for_how_questions(client, auth, svc, monkeypatch):
    spy = Spy().install(monkeypatch)
    c = new_chat(client, auth)
    r = say(client, auth, c["id"], "How does the user endpoint assign roles?")
    sent = spy.calls[0]
    assert "thorough walkthrough" in sent["system"] and sent["max_tokens"] >= 2500
    assert "POST /api/auth/users" in sent["prompt"] and "authorize" in sent["prompt"]
    files = {s["file"] for s in r.json()["items"][-1]["sources"]}
    assert {"auth.py", "policy.py"} <= files


def test_chat_keeps_simple_lookups_short(client, auth, svc, monkeypatch):
    spy = Spy().install(monkeypatch)
    c = new_chat(client, auth)
    say(client, auth, c["id"], "How many users does create_user save?")
    assert "thorough walkthrough" not in spy.calls[0]["system"]


def test_the_knowledge_box_uses_the_walkthrough_for_how_questions_and_stays_brief_otherwise(
    client, auth, svc, monkeypatch
):
    spy = Spy().install(monkeypatch)
    r = client.post("/api/knowledge/ask", headers=auth, json={"query": "How does the user endpoint assign roles?"})
    assert r.status_code == 200
    assert "thorough walkthrough" in spy.calls[0]["system"] and "Be concise" not in spy.calls[0]["system"]
    assert spy.calls[0]["max_tokens"] == wt.MAX_TOKENS_WALKTHROUGH
    assert "POST /api/auth/users" in spy.calls[0]["prompt"]
    assert {s["file"] for s in r.json()["sources"]} >= {"auth.py", "policy.py"}
    client.post("/api/knowledge/ask", headers=auth, json={"query": "How many roles exist for create_user?"})
    assert "Be concise" in spy.calls[1]["system"] and spy.calls[1]["max_tokens"] == 900


def test_the_knowledge_box_names_a_model_failure_instead_of_showing_loose_passages(client, auth, svc, monkeypatch):
    def boom(this, *a, **k):
        raise RuntimeError("api.example.com returned 429: quota exceeded")

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", boom)
    r = client.post("/api/knowledge/ask", headers=auth, json={"query": "Where is authorize defined?"}).json()
    assert r["mode"] == "failed" and r["answer"].startswith("**The AI model could not answer.**")
    assert "limiting requests" in r["answer"]


@pytest.mark.parametrize(
    "q,failure",
    [
        ("Write an API for the repolens to list the admin users?", False),
        ("Explain how user roles are assigned", False),
        ("KeyError: 'name' in rows", True),
        ("Profile page returns 500 for deleted users", True),
        ("How does the login flow work and why does it fail for SSO users?", True),
        ("deleted users profile blank", True),  # not clearly a request: investigate
    ],
)
def test_only_requests_that_describe_no_failure_are_not_investigated(q, failure):
    from repolens.agents.workflow import is_failure_report

    assert is_failure_report(q, False) is failure


def test_a_feature_request_gets_no_root_cause_and_low_confidence(client, auth, svc):
    inv = client.post(
        "/api/investigations",
        headers=auth,
        json={"query": "Write an API to list the admin users", "repo_ids": [svc["id"]]},
    ).json()
    got = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    assert got["status"] == "completed"
    assert "not a failure to investigate" in got["root_cause"]
    assert got["confidence"] <= 0.2 and all(h["status"] == "unverified" for h in got["hypotheses"])
    assert "Use Ask" in got["report_md"] or "Open Ask" in got["report_md"]


def test_a_build_request_is_grounded_in_the_existing_route_and_never_replayed_from_memory(
    client, auth, svc, monkeypatch
):
    spy = Spy().install(monkeypatch)
    q = {"query": "Write an API to list the admin users"}
    client.post("/api/knowledge/ask", headers=auth, json=q)
    client.post("/api/knowledge/ask", headers=auth, json=q)
    assert len(spy.calls) == 2  # asked twice, answered twice: written to order, not replayed
    assert "Illustrative, not from your repository" in spy.calls[0]["system"]
    assert "use only modules, functions and settings that appear there" in spy.calls[0]["system"]
    assert "GET /api/auth/users" in spy.calls[0]["prompt"] and "create_user" in spy.calls[0]["prompt"]
    assert spy.calls[0]["max_tokens"] == wt.MAX_TOKENS_BUILD


def test_wiki_ask_includes_code_unless_asked_for_documents_only(client, auth, svc, monkeypatch):
    spy = Spy().install(monkeypatch)
    body = {"query": "How does the user endpoint assign roles?"}
    r = client.post("/api/wiki/ask", headers=auth, json=body).json()
    assert "auth.py" in {s["file"] for s in r["sources"]}
    r = client.post("/api/wiki/ask", headers=auth, json={**body, "docs_only": True}).json()
    assert all(s["kind"] == "doc" for s in r["sources"])
    _ = spy


def test_task_types():
    assert wt.task_kind("Write an API for the repolens to list the admin users?") == "build"
    assert wt.task_kind("Please create a runbook for restores") == "build"
    assert wt.task_kind("How does the user endpoint assign roles?") == "explain"
    assert wt.task_kind("How many times does it retry?") == "lookup"
    assert wt.task_kind("Where is the retry policy documented?") == "lookup"
    assert wt.task_kind("How does the system add roles?") == "explain"  # 'add' mid-sentence is not an instruction
