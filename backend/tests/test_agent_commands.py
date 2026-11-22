"""Commands in a pull request comment, and the record of webhook deliveries."""

import hashlib
import hmac
import json

import pytest

from repolens.models import AgentDraft, AgentRun, PersonaRun, WebhookDelivery
from repolens.services import agent_commands, knowledge
from tests.test_automations import CLONE, ci, inbox, index_it, turn_on_pr  # noqa: F401  (ci is a fixture)
from tests.test_personas import model  # noqa: F401  (fixture: a fake model)


def comment(client, body, assoc="MEMBER", number=7, cid=900, action="created", sender="User", pull=True):
    payload = {
        "action": action,
        "issue": {
            "number": number,
            "title": "Fix the user lookup",
            "html_url": f"https://github.com/acme/shop/pull/{number}",
            **({"pull_request": {"url": "x"}} if pull else {}),
        },
        "comment": {
            "id": cid,
            "body": body,
            "author_association": assoc,
            "user": {"login": "ana", "type": sender},
            "html_url": f"https://github.com/acme/shop/pull/{number}#issuecomment-{cid}",
        },
        "sender": {"type": sender},
        "repository": {"clone_url": CLONE, "full_name": "acme/shop"},
    }
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
    hdr = {"X-GitHub-Event": "issue_comment", "Content-Type": "application/json", "X-Hub-Signature-256": sig}
    r = client.post("/api/webhooks/github", content=raw, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_command_is_read_from_the_first_line_only():
    assert agent_commands.parse("/repolens review") == ("review", "")
    assert agent_commands.parse("\n  /RepoLens ask how does login work?\nthanks") == ("ask", "how does login work?")
    assert agent_commands.parse("please /repolens review") is None
    assert agent_commands.parse("looks good\n/repolens review") is None
    assert agent_commands.parse("/repolens reviewing") is None


def test_a_review_command_reviews_the_pull_request_and_leaves_a_draft(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    assert len(comment(client, "/repolens review")["started"]) == 1
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.event == "pr_review" and run.event_key == "7-c900" and run.status == "started"
    assert "wrote `/repolens review`" in run.detail["trace"][0]["what"]
    assert session.query(PersonaRun).count() == 2 and len(inbox(client, auth)) == 1
    assert "posted" not in ci  # nothing went to GitHub


def test_only_a_trusted_person_can_give_a_command_and_never_a_bot(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    assert comment(client, "/repolens review", assoc="NONE")["command"] == "ignored"
    assert comment(client, "/repolens review", assoc="CONTRIBUTOR", cid=901)["command"] == "ignored"
    assert comment(client, "/repolens review", sender="Bot", cid=902)["command"] == "ignored"
    assert session.query(AgentRun).count() == 0
    for assoc in ("OWNER", "COLLABORATOR"):
        assert comment(client, "/repolens review", assoc=assoc, cid=903 if assoc == "OWNER" else 904)["started"]


def test_a_comment_on_an_issue_or_a_plain_comment_does_nothing(client, auth, ci, session, model):
    turn_on_pr(client, auth)
    assert comment(client, "/repolens review", pull=False)["command"] == "ignored"
    assert comment(client, "nice work")["command"] == "none"
    assert comment(client, "/repolens review", action="edited")["command"] == "ignored"
    assert session.query(AgentRun).count() == 0


def test_commands_need_the_pull_request_rule_to_be_on_and_obey_its_limit(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    assert comment(client, "/repolens review")["started"] == []  # no rule
    turn_on_pr(client, auth, daily_limit=1)
    assert len(comment(client, "/repolens review", cid=1)["started"]) == 1
    assert comment(client, "/repolens review", cid=2)["started"] == []  # the limit is used
    skipped = session.query(AgentRun).filter_by(status="skipped").one()
    assert "limit of 1" in skipped.reason
    assert len(session.query(AgentRun).all()) == 2


def test_the_same_comment_is_handled_once(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    comment(client, "/repolens review", cid=5)
    assert comment(client, "/repolens review", cid=5)["started"] == []
    assert session.query(AgentRun).count() == 1


def test_an_ask_command_leaves_the_answer_as_a_draft_with_its_question(client, auth, ci, session, model, monkeypatch):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    seen = {}

    def fake_ask(db, org, question, repo_ids, **kw):
        seen.update(question=question, repo_ids=repo_ids)
        return {"mode": "llm", "answer": "It checks the token first.", "sources": [{"file": "app/auth.py"}]}

    monkeypatch.setattr(knowledge, "ask", fake_ask)
    assert len(comment(client, "/repolens ask how does login work?")["started"]) == 1
    assert seen["question"] == "how does login work?" and seen["repo_ids"] == [ci["repo"]["id"]]
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.event_key == "ask-7-900" and run.status == "started" and run.detail["question"] == "how does login work?"
    (d,) = session.query(AgentDraft).all()
    assert d.kind == "pr_comment" and "It checks the token first." in d.body and "`app/auth.py`" in d.body
    assert d.meta["number"] == 7 and d.status == "new"
    shown = client.get("/api/automations", headers=auth).json()["runs"][0]
    assert shown["retry"] == "answer_again"
    assert "posted" not in ci


def test_an_ask_that_the_model_cannot_answer_fails_with_the_reason_and_can_be_tried_again(
    client, auth, ci, session, model, monkeypatch
):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    monkeypatch.setattr(knowledge, "ask", lambda *a, **k: {"mode": "failed", "answer": "", "sources": []})
    comment(client, "/repolens ask why?")
    session.expire_all()
    run = session.query(AgentRun).one()
    assert run.status == "failed" and "could not write an answer" in run.reason
    assert session.query(AgentDraft).count() == 0
    monkeypatch.setattr(knowledge, "ask", lambda *a, **k: {"mode": "llm", "answer": "Because.", "sources": []})
    assert client.post(f"/api/automations/runs/{run.id}/retry", headers=auth).status_code == 200
    session.expire_all()
    assert session.query(AgentRun).one().status == "started" and session.query(AgentDraft).one().body.endswith(
        "Because."
    )


def test_an_ask_without_a_question_is_ignored(client, auth, ci, session, model):
    turn_on_pr(client, auth)
    assert comment(client, "/repolens ask")["command"] == "ignored"
    assert session.query(AgentRun).count() == 0


# ------------------------------------------------------------------------------------------------ the delivery record
def deliveries(client, auth):
    return client.get("/api/automations/webhook", headers=auth).json()


def test_a_signed_ping_is_recorded_as_a_working_connection(client, auth, ci, session):
    raw = b'{"zen": "Keep it logically awesome."}'
    sig = "sha256=" + hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/webhooks/github",
        content=raw,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sig, "X-GitHub-Delivery": "d-1"},
    )
    assert r.status_code == 200 and r.json() == {"pong": True}
    got = deliveries(client, auth)
    assert got["secret_set"] is True and got["path"] == "/api/webhooks/github"
    (row,) = got["deliveries"]
    assert row["event"] == "ping" and row["outcome"] == "ok" and "secret matches" in row["note"]
    assert got["last_delivery_at"]


def test_a_wrong_secret_is_recorded_with_the_reason_without_the_payload(client, auth, ci, session):
    r = client.post(
        "/api/webhooks/github",
        content=b'{"x": 1}',
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert r.status_code == 401
    (row,) = deliveries(client, auth)["deliveries"]
    assert row["outcome"] == "rejected" and "secret on GitHub differs" in row["note"] and row["repo"] == ""
    assert deliveries(client, auth)["last_delivery_at"] is None


def test_events_are_recorded_with_what_was_done_and_unused_ones_as_ignored(client, auth, ci, session, model):
    index_it(session, ci["repo"]["id"])
    turn_on_pr(client, auth)
    ci["send_pr"]()
    ci["send"](conclusion="success")
    comment(client, "nice work")
    rows = {(d["event"], d["action"]): d for d in deliveries(client, auth)["deliveries"]}
    assert (
        rows[("pull_request", "opened")]["outcome"] == "ok" and "review=1" in rows[("pull_request", "opened")]["note"]
    )
    assert rows[("pull_request", "opened")]["repo"] == "acme/shop"
    assert rows[("workflow_run", "completed")]["outcome"] == "ok"
    assert rows[("issue_comment", "created")]["note"] == "command=none"


def test_an_event_nobody_uses_is_marked_ignored(client, auth, ci):
    raw = b'{"action": "starred", "repository": {"clone_url": "%s", "full_name": "acme/shop"}}' % CLONE.encode()
    sig = "sha256=" + hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
    client.post("/api/webhooks/github", content=raw, headers={"X-GitHub-Event": "star", "X-Hub-Signature-256": sig})
    (row,) = deliveries(client, auth)["deliveries"]
    assert row["outcome"] == "ignored" and "does not use star events" in row["note"]


def test_the_self_test_checks_the_receiver_and_says_it_is_not_from_github(client, auth, ci):
    r = client.post("/api/automations/webhook/test", headers=auth)
    assert r.status_code == 200
    (row,) = r.json()["deliveries"]
    assert row["event"] == "selftest" and row["outcome"] == "ok" and "not sent by GitHub" in row["note"]
    assert r.json()["last_delivery_at"] is None  # only a real delivery counts as GitHub reaching RepoLens


def test_the_self_test_says_so_when_no_secret_is_set(client, auth, monkeypatch):
    from repolens.config import get_settings

    monkeypatch.delenv("REPOLENS_GITHUB_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()
    assert client.post("/api/automations/webhook/test", headers=auth).status_code == 409
    assert client.get("/api/automations/webhook", headers=auth).json()["secret_set"] is False


def test_only_admins_see_the_record_and_it_is_kept_short(client, auth, ci, session):
    from tests.test_chat import make_user

    member = make_user(client, auth, "dev@acme.io")
    assert client.get("/api/automations/webhook", headers=member).status_code == 403
    assert client.post("/api/automations/webhook/test", headers=member).status_code == 403
    from repolens.services import webhook_log

    for i in range(webhook_log.KEEP + 20):
        session.add(WebhookDelivery(org_id="", event=f"e{i}", outcome="ignored"))
    session.commit()
    webhook_log.prune(session)
    assert session.query(WebhookDelivery).count() <= webhook_log.KEEP
    assert len(deliveries(client, auth)["deliveries"]) == webhook_log.SHOWN


@pytest.mark.parametrize("path", ["/api/automations/webhook"])
def test_another_workspace_does_not_see_the_record(client, auth, ci, path):
    from tests.conftest import register

    client.post("/api/automations/webhook/test", headers=auth)
    other = register(client, "x@other.io", "Other")
    assert client.get(path, headers=other).json()["deliveries"] == []
