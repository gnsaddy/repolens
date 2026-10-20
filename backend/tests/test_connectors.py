import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from repolens.models import Document, Repository, WikiSource
from repolens.services import connectors
from repolens.services import wiki_import as wi
from repolens.services.connectors import slack
from tests.conftest import register
from tests.test_api import configure_jira, jira_transport  # noqa: F401


@pytest.fixture(autouse=True)
def clean_transports():
    yield
    connectors.transports.clear()


# ------------------------------------------------------------------------------------------------ Jira
def issues(n=2, updated="2026-03-01T10:00:00.000+0000", summary="Profile 500"):
    return [
        {
            "key": f"PAY-{i}",
            "fields": {
                "summary": f"{summary} {i}",
                "status": {"name": "Done"},
                "issuetype": {"name": "Bug"},
                "priority": {"name": "High"},
                "resolution": {"name": "Fixed"},
                "labels": ["regression"],
                "components": [{"name": "billing"}],
                "assignee": {"displayName": "Ana"},
                "updated": updated,
                "description": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Totals were rounded wrongly. token ghp_" + "a" * 36}],
                        }
                    ],
                },
                "comment": {
                    "comments": [
                        {
                            "author": {"displayName": "Bo"},
                            "created": "2026-03-02T09:00:00.000+0000",
                            "body": "Fixed by the decimal refactor",
                        }
                    ]
                },
            },
        }
        for i in range(1, n + 1)
    ]


def jira_search(state):
    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path.endswith("/myself"):
            return httpx.Response(200, json={"displayName": "Bot"})
        if request.url.path.endswith("/search/jql"):
            if request.url.params.get("nextPageToken") == "p2":
                return httpx.Response(200, json={"issues": state["items"][1:]})
            return httpx.Response(200, json={"issues": state["items"][:1], "nextPageToken": "p2"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_jira_issues_become_documents_incrementally(client, auth, session):
    configure_jira(client, auth)
    state = {"requests": [], "items": issues(2)}
    connectors.transports["jira"] = jira_search(state)
    body = {"config": {"jql": "project = PAY AND issuetype = Bug"}, "name": "Payments bugs"}
    assert client.post("/api/wiki/connectors/jira/check", headers=auth, json=body).json() == {
        "ok": True,
        "detail": "Connected to Jira as Bot",
    }
    r = client.post("/api/wiki/connectors/jira/import", headers=auth, json=body)
    assert r.status_code == 202, r.text
    repo_id = r.json()["repo_id"]
    docs = {d.source_id: d for d in session.query(Document).filter_by(repo_id=repo_id)}
    assert set(docs) == {"PAY-1", "PAY-2"}  # followed the second result page
    d = docs["PAY-1"]
    assert (
        d.path == "jira/pay/PAY-1.md"
        and d.source_type == "jira"
        and d.source_url == "https://acme.atlassian.net/browse/PAY-1"
    )
    assert "**Status:** Done" in d.content and "Fixed by the decimal refactor" in d.content and "Bo" in d.content
    assert "ghp_" not in d.content  # secrets are redacted before storing
    repo = session.get(Repository, repo_id)
    assert repo.visibility == "restricted" and repo.status == "ready"

    # a second sync with one ticket edited and one deleted
    state["items"] = issues(1, updated="2026-04-01T10:00:00.000+0000", summary="Profile 500 (edited)")
    sid = session.query(WikiSource).filter_by(repo_id=repo_id).one().id
    assert client.post(f"/api/wiki/sources/{sid}/sync", headers=auth).status_code == 202
    session.expire_all()
    docs = {d.source_id: d for d in session.query(Document).filter_by(repo_id=repo_id)}
    assert set(docs) == {"PAY-1"} and "(edited)" in docs["PAY-1"].title
    src = session.get(WikiSource, sid)
    assert src.last_status == "completed" and src.pages_total == 1
    # tickets cannot be deleted by hand: they come back on the next sync
    r = client.delete("/api/wiki/page", headers=auth, params={"repo_id": repo_id, "path": docs["PAY-1"].path})
    assert r.status_code == 409 and "jira" in r.json()["detail"]


def test_jira_requires_a_connection_and_validates_jql(client, auth):
    r = client.post("/api/wiki/connectors/jira/check", headers=auth, json={"config": {"jql": "project = X"}})
    assert r.json()["ok"] is False and "Settings" in r.json()["error"]
    assert (
        client.post("/api/wiki/connectors/jira/import", headers=auth, json={"config": {"jql": ""}}).status_code == 400
    )
    assert client.post("/api/wiki/connectors/nope/check", headers=auth, json={}).status_code == 404


# ------------------------------------------------------------------------------------------------ Slack
def slack_api(state):
    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append((request.url.path, dict(request.url.params), request.headers.get("authorization")))
        m = request.url.path.rsplit("/", 1)[-1]
        q = request.url.params
        if request.headers.get("authorization") != "Bearer xoxb-good":
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        if m == "auth.test":
            return httpx.Response(
                200, json={"ok": True, "team": "Acme", "user": "repolens", "url": "https://acme.slack.com/"}
            )
        if m == "users.list":
            return httpx.Response(
                200, json={"ok": True, "members": [{"id": "U1", "real_name": "Ana"}, {"id": "U2", "name": "bo"}]}
            )
        if m == "conversations.list":
            return httpx.Response(
                200, json={"ok": True, "channels": [{"id": "C1", "name": "incidents"}, {"id": "C2", "name": "random"}]}
            )
        if m == "conversations.history":
            assert q["channel"] == "C1"
            return httpx.Response(200, json={"ok": True, "messages": state["messages"]})
        if m == "conversations.replies":
            return httpx.Response(200, json={"ok": True, "messages": state["replies"][q["ts"]]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_slack_threads_and_digests(client, auth, session):
    now = datetime.now(UTC)
    ts = lambda mins: f"{(now - timedelta(minutes=mins)).timestamp():.6f}"  # noqa: E731
    root = {
        "ts": ts(60),
        "user": "U1",
        "text": "Profile endpoint 500s for <@U2>'s deleted account, see <https://x.io/log|the log> in <#C1>",
        "reply_count": 2,
    }
    state = {
        "calls": [],
        "messages": [
            root,
            {"ts": ts(30), "user": "U2", "text": "lunch?"},
            {"ts": ts(29), "user": "U1", "text": "yes"},
            {"ts": ts(28), "user": "U2", "text": "noon"},
            {"ts": ts(5), "subtype": "channel_join", "user": "U2", "text": "joined"},
        ],
        "replies": {
            root["ts"]: [
                root,
                {"ts": ts(50), "user": "U2", "text": "Null guard missing after the refactor"},
                {"ts": ts(40), "user": "U1", "text": "Fixed in #1234, token xox" + "b-123456789012-abcdefghijklmnop"},
            ]
        },
    }
    connectors.transports["slack"] = slack_api(state)
    body = {"config": {"channels": "#incidents", "days": 30}, "secret": "xoxb-good", "name": "Incidents chat"}
    assert (
        client.post("/api/wiki/connectors/slack/check", headers=auth, json=body).json()["detail"]
        == "Connected to Slack workspace Acme as repolens"
    )
    bad = client.post("/api/wiki/connectors/slack/check", headers=auth, json={**body, "secret": "xoxb-bad"}).json()
    assert bad == {"ok": False, "error": "Slack: the token is invalid or revoked"}
    r = client.post("/api/wiki/connectors/slack/import", headers=auth, json=body)
    assert r.status_code == 202, r.text
    docs = session.query(Document).filter_by(repo_id=r.json()["repo_id"]).order_by(Document.path).all()
    kinds = {("digest" if "digest" in d.path else "thread"): d for d in docs}
    assert set(kinds) == {"digest", "thread"}  # the thread and the lunch chatter; the join message is dropped
    t = kinds["thread"]
    assert "@bo" in t.content and "[the log](https://x.io/log)" in t.content and "#incidents" in t.content
    assert "Null guard missing" in t.content and "xoxb-1234" not in t.content  # redacted
    assert t.source_url.startswith("https://acme.slack.com/archives/C1/p")
    assert "lunch?" in kinds["digest"].content and "joined" not in kinds["digest"].content
    assert all("xoxb" not in json.dumps(c[1]) for c in state["calls"])  # the token travels only in the header
    # the token is stored encrypted and never returned
    src = session.query(WikiSource).filter_by(repo_id=r.json()["repo_id"]).one()
    assert src.secret_encrypted and "xoxb" not in src.secret_encrypted
    assert "xoxb" not in json.dumps(client.get("/api/wiki/sources", headers=auth).json())


def test_slack_validation_and_unknown_channel(client, auth):
    for config in ({"channels": ""}, {"channels": "a b"}, {"channels": ",".join(f"c{i}" for i in range(25))}):
        assert (
            client.post(
                "/api/wiki/connectors/slack/import", headers=auth, json={"config": config, "secret": "xoxb-good"}
            ).status_code
            == 400
        )
    assert (
        client.post("/api/wiki/connectors/slack/import", headers=auth, json={"config": {"channels": "x"}}).status_code
        == 400
    )  # token required
    state = {"calls": [], "messages": [], "replies": {}}
    connectors.transports["slack"] = slack_api(state)
    r = client.post(
        "/api/wiki/connectors/slack/import",
        headers=auth,
        json={"config": {"channels": "missing"}, "secret": "xoxb-good"},
    )
    sources = client.get("/api/wiki/sources", headers=auth).json()
    assert (
        r.status_code == 202 and sources[0]["status"] == "failed" and "'missing' was not found" in sources[0]["error"]
    )


def test_slack_export_import(client, auth, session):
    users = json.dumps([{"id": "U1", "real_name": "Ana"}])
    day = json.dumps(
        [
            {"ts": "1767225600.000100", "user": "U1", "text": "Deploy failed with exit 137", "reply_count": 1},
            {
                "ts": "1767225700.000200",
                "user": "U1",
                "text": "OOM killed, raised the limit",
                "thread_ts": "1767225600.000100",
            },
        ]
    )
    r = client.post(
        "/api/wiki/slack-export",
        headers=auth,
        json={
            "name": "Old chat",
            "files": [{"path": "users.json", "content": users}, {"path": "ops/2026-01-01.json", "content": day}],
        },
    )
    assert r.status_code == 201 and r.json()["documents"] == 1
    d = session.query(Document).filter_by(repo_id=r.json()["repo_id"]).one()
    assert (
        "Deploy failed" in d.content
        and "OOM killed" in d.content
        and "**Ana**" in d.content
        and d.source_type == "upload"
    )
    assert (
        client.post(
            "/api/wiki/slack-export", headers=auth, json={"files": [{"path": "ops/x.json", "content": "nope"}]}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/wiki/slack-export", headers=auth, json={"files": [{"path": "users.json", "content": "[]"}]}
        ).status_code
        == 400
    )


def test_mrkdwn():
    assert (
        slack.mrkdwn("hi <@U1> see <#C1|ops> &amp; <https://a.io|A> <!here>", {"U1": "Ana"}, {})
        == "hi @Ana see #ops & [A](https://a.io) @here"
    )


# ------------------------------------------------------------------------------------------------ schedule
def test_schedule_endpoint_and_due_logic(client, auth, session):
    configure_jira(client, auth)
    state = {"requests": [], "items": issues(1)}
    connectors.transports["jira"] = jira_search(state)
    r = client.post(
        "/api/wiki/connectors/jira/import",
        headers=auth,
        json={"config": {"jql": "project = PAY"}, "sync_interval_hours": 6},
    )
    sid = session.query(WikiSource).one().id
    session.expire_all()
    src = session.get(WikiSource, sid)
    assert src.sync_interval_hours == 6 and src.next_sync_at is not None  # scheduled after the first sync
    now = datetime.now(UTC)
    assert wi.due_sources(session, now) == []
    assert wi.due_sources(session, now + timedelta(hours=7)) == [sid]
    src.last_status = "running"
    session.commit()
    assert wi.due_sources(session, now + timedelta(hours=7)) == []  # never two syncs at once
    src.last_status = "completed"
    session.commit()
    s = client.put(f"/api/wiki/sources/{sid}/schedule", headers=auth, json={"sync_interval_hours": 0}).json()
    assert (
        s == {"sync_interval_hours": 0, "next_sync_at": None}
        and wi.due_sources(session, now + timedelta(days=30)) == []
    )
    assert (
        client.put(f"/api/wiki/sources/{sid}/schedule", headers=auth, json={"sync_interval_hours": 9999}).status_code
        == 422
    )
    other = register(client, "x@other.io", "Other")
    assert (
        client.put(f"/api/wiki/sources/{sid}/schedule", headers=other, json={"sync_interval_hours": 1}).status_code
        == 404
    )
    kinds = {c["kind"]: c for c in client.get("/api/wiki/connectors", headers=auth).json()}
    assert {"jira", "slack"} <= set(kinds) and any(f["secret"] for f in kinds["slack"]["fields"])
    assert r.status_code == 202
