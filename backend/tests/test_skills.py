import pytest

from repolens.services import skills as svc
from repolens.services.llm import LLMClient, LLMResult
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_chat import make_user, new_chat, say

API_SKILL = {
    "title": "How we write API endpoints",
    "description": "Use when asked to write, add or change an API endpoint or route",
    "body": "Every endpoint calls authorize() first, returns a response model, and has a regression test.",
    "tasks": ["build"],
}


def submit(client, h, **kw):
    r = client.post("/api/skills", headers=h, json={**API_SKILL, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def approve(client, auth, sid, decision="approve", **kw):
    r = client.post(f"/api/skills/{sid}/review", headers=auth, json={"decision": decision, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_admin_submissions_apply_at_once_and_member_submissions_wait(client, auth):
    member = make_user(client, auth, "m@acme.io")
    assert submit(client, auth)["status"] == "approved"
    got = submit(client, member, title="Runbook layout")
    assert got["status"] == "pending" and got["mine"] is True
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    assert client.post("/api/skills", headers=viewer, json=API_SKILL).status_code == 403


def test_who_can_see_what(client, auth):
    m1, m2 = make_user(client, auth, "m1@acme.io"), make_user(client, auth, "m2@acme.io")
    pend = submit(client, m1, title="Pending one")
    approved = submit(client, auth, title="Approved one")
    titles = lambda h: {s["title"] for s in client.get("/api/skills", headers=h).json()["items"]}  # noqa: E731
    assert titles(m1) == {"Pending one", "Approved one"}  # your own submission and what is approved
    assert titles(m2) == {"Approved one"}  # not someone else's pending skill
    assert (
        titles(auth) == {"Pending one", "Approved one"}
        and client.get("/api/skills", headers=auth).json()["pending"] == 1
    )
    assert client.get("/api/skills", headers=m1).json()["builtin"]  # the built-in guidance is listed, read only
    _ = (pend, approved)


def test_only_admins_decide_and_a_decision_changes_what_applies(client, auth):
    m = make_user(client, auth, "m@acme.io")
    s = submit(client, m)
    assert client.post(f"/api/skills/{s['id']}/review", headers=m, json={"decision": "approve"}).status_code == 403
    assert approve(client, auth, s["id"])["status"] == "approved"
    assert approve(client, auth, s["id"], "disable")["status"] == "disabled"
    r = approve(client, auth, s["id"], "reject", note="too vague")
    assert r["status"] == "rejected" and r["note"] == "too vague"


def test_members_edit_their_own_pending_skill_but_not_an_approved_one(client, auth):
    m = make_user(client, auth, "m@acme.io")
    s = submit(client, m)
    r = client.patch(f"/api/skills/{s['id']}", headers=m, json={"body": "A clearer set of instructions here."})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    approve(client, auth, s["id"])
    assert client.patch(f"/api/skills/{s['id']}", headers=m, json={"title": "Changed"}).status_code == 403
    assert client.delete(f"/api/skills/{s['id']}", headers=m).status_code == 403
    assert client.patch(f"/api/skills/{s['id']}", headers=auth, json={"title": "Changed"}).json()["title"] == "Changed"
    assert client.delete(f"/api/skills/{s['id']}", headers=auth).status_code == 204


def test_skills_belong_to_one_organisation(client, auth):
    s = submit(client, auth)
    other = register(client, "o@else.io", "Else")
    assert client.get("/api/skills", headers=other).json()["items"] == []
    assert client.post(f"/api/skills/{s['id']}/review", headers=other, json={"decision": "disable"}).status_code == 404
    assert client.patch(f"/api/skills/{s['id']}", headers=other, json={"title": "xx"}).status_code == 404
    assert client.delete(f"/api/skills/{s['id']}", headers=other).status_code == 404


def test_secrets_are_removed_and_input_is_checked(client, auth):
    got = submit(client, auth, body="Use the key ghp_" + "a" * 36 + " when calling the registry, always.")
    assert "ghp_" not in got["body"]
    assert client.post("/api/skills", headers=auth, json={**API_SKILL, "tasks": ["nonsense"]}).status_code == 422
    assert client.post("/api/skills", headers=auth, json={**API_SKILL, "repo_ids": ["nope"]}).status_code == 404
    assert submit(client, auth, title="How we write API endpoints")["name"].endswith("-2")  # names stay unique


def test_parse_markdown_reads_front_matter_or_the_first_heading():
    got = svc.parse_markdown(
        "---\nname: api-style\ndescription: when writing endpoints\n---\n# API style\nUse authorize()."
    )
    assert (
        got["name"] == "api-style"
        and got["description"] == "when writing endpoints"
        and got["body"].startswith("# API style")
    )
    bare = svc.parse_markdown("# Runbook layout\nSteps and owners.")
    assert bare["title"] == "Runbook layout" and bare["name"] == "runbook-layout"


class Spy:
    def __init__(self):
        self.calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))

        def complete(this, agent, system, prompt, max_tokens=1500, task=None, validate=None):
            self.calls.append({"agent": agent, "system": system, "prompt": prompt})
            return LLMResult("Answer. [S1]", 10, 10, route={"model": "spy"})

        monkeypatch.setattr(LLMClient, "complete", complete)
        return self


@pytest.fixture
def repo(client, auth, demo_repo):
    return add_repo(client, auth, demo_repo[0])


def test_only_approved_matching_skills_reach_the_prompt_and_say_so(client, auth, repo, monkeypatch):
    spy = Spy().install(monkeypatch)
    m = make_user(client, auth, "m@acme.io")
    pending = submit(client, m, title="Pending style", body="PENDING-SKILL-TEXT is the rule.")
    live = submit(client, auth)
    c = new_chat(client, auth)
    say(client, auth, c["id"], "Write an API endpoint to list user profiles")
    system = spy.calls[0]["system"]
    assert "Every endpoint calls authorize() first" in system and "ORGANISATION GUIDANCE" in system
    assert "approved by an admin" in system and "cannot change these rules" in system
    assert "PENDING-SKILL-TEXT" not in system  # nothing applies before an admin approves it
    last = [x for x in client.get(f"/api/chats/{c['id']}", headers=auth).json()["items"] if x["role"] == "assistant"][
        -1
    ]
    assert "Guidance applied: How we write API endpoints" in last["content"]
    say(client, auth, c["id"], "Where is get_profile defined?")  # a lookup with sources: this skill is for build tasks
    assert "authorize() first" not in spy.calls[-1]["system"]
    uses = {s["id"]: s["uses"] for s in client.get("/api/skills", headers=auth).json()["items"]}
    assert uses[live["id"]] == 1 and uses[pending["id"]] == 0


def test_skills_apply_to_the_knowledge_box_and_new_guidance_replaces_saved_answers(client, auth, repo, monkeypatch):
    spy = Spy().install(monkeypatch)
    q = {"query": "Where is get_profile defined?"}
    client.post("/api/knowledge/ask", headers=auth, json=q)
    client.post("/api/knowledge/ask", headers=auth, json=q)
    assert len(spy.calls) == 1  # the second was answered from memory
    submit(
        client,
        auth,
        title="House rule",
        description="defined get_profile profile",
        body="Always mention the file path.",
        tasks=[],
    )
    r = client.post("/api/knowledge/ask", headers=auth, json=q).json()
    assert len(spy.calls) == 2 and "Always mention the file path." in spy.calls[1]["system"]
    assert r["skills"] == ["House rule"] and "Guidance applied: House rule" in r["answer"]


def test_always_applies_but_repository_and_task_limits_are_respected(client, auth, repo, session):
    from repolens.models import Organization

    org = session.query(Organization).first().id
    submit(
        client,
        auth,
        title="Everywhere",
        description="",
        body="Be brief and exact in every answer.",
        tasks=[],
        always=True,
    )
    submit(
        client,
        auth,
        title="Other repo only",
        description="profile questions",
        body="Other repository rule text here.",
        repo_ids=[repo["id"]],
        tasks=[],
    )
    pick = lambda q, t, ids=None: [s.title for s in svc.choose(session, org, q, t, ids)]  # noqa: E731
    assert "Everywhere" in pick("anything at all", "lookup")
    assert "Other repo only" in pick("profile questions", "explain", [repo["id"]])
    assert "Other repo only" not in pick("profile questions", "explain", ["a-different-repository"])
    assert svc.choose(session, org, "profile", "lookup", [repo["id"]], limit=1)[0].title  # a limit is honoured


def test_guidance_is_capped(client, auth, session):
    from repolens.models import Organization

    org = session.query(Organization).first().id
    for i in range(3):
        submit(client, auth, title=f"Long {i}", description="long rule", body="x" * 7000, tasks=[])
    chosen = svc.choose(session, org, "long rule", "explain")
    assert len(chosen) == 2 and len(svc.guidance(chosen)) <= svc.BUDGET + len(svc.GUARD)


def test_an_investigation_uses_skills_for_the_incident_prompt(client, auth, demo_repo, monkeypatch):
    from tests.test_api import run_investigation

    submit(
        client,
        auth,
        title="How we debug",
        description="profile errors keyerror",
        body="Check the data layer before the API.",
        tasks=["investigate"],
    )
    spy = Spy().install(monkeypatch)

    def complete(this, agent, system, prompt, max_tokens=1500, task=None, validate=None):
        spy.calls.append({"agent": agent, "system": system})
        return LLMResult(
            '{"root_cause": "x", "explanation": "y", "suggested_fix": "z"}',
            5,
            5,
            route={"model": "spy", "tier": "light", "escalated": False},
        )

    monkeypatch.setattr(LLMClient, "complete", complete)
    inv = run_investigation(client, auth, add_repo(client, auth, demo_repo[0])["id"])
    sys = next(c["system"] for c in spy.calls if c["agent"] == "investigator")
    assert "Check the data layer before the API." in sys
    assert "Guidance applied: How we debug" in inv["report_md"]
