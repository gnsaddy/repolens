import httpx
import pytest

from repolens.api import jira as jira_api
from repolens.services import jira_analysis as ja
from repolens.services.jira import JiraIssue
from tests.test_api import configure_jira  # noqa: F401

TRACE = 'Traceback (most recent call last):\n  File "src/api.py", line 4, in handle_profile_request\n  File "src/user_profile.py", line 10, in get_profile\nKeyError: \'name\''


def issue(**kw):
    base = dict(
        key="BUG-7",
        summary="Profile endpoint returns 500 for deleted users",
        description="Happens when the user is deleted.\n" + TRACE,
        status="Open",
        issue_type="Bug",
        priority="High",
        comments=[],
        labels=[],
        components=["user-service"],
        url="https://x/browse/BUG-7",
        reporter="Ana",
        assignee="",
        created="2026-09-01T10:00:00.000+0000",
        updated="2026-09-02T10:00:00.000+0000",
    )
    base.update(kw)
    items = kw.get("comment_items", [])
    base["comments"] = [c["body"] for c in items]
    return JiraIssue(**base)


COMMENTS = [
    {
        "author": "Ben",
        "created": "2026-09-01",
        "body": "Can you share the exact request? Does `UserProfileService.get_profile` return None?",
    },
    {
        "author": "Ana",
        "created": "2026-09-01",
        "body": "I tried restarting the service and it did not help. Workaround: we temporarily disabled the cache.",
    },
    {
        "author": "Cy",
        "created": "2026-09-02",
        "body": "Root cause: deleted users have no name because the row is soft-deleted. Fix in https://github.com/acme/api/pull/42 merged.",
    },
]


def test_analysis_reads_description_and_comments():
    d = ja.analyze(issue(comment_items=COMMENTS))
    s, disc = d["signals"], d["discussion"]
    assert "KeyError" in s["errors"] and any(
        f["file"] == "src/user_profile.py" and f.get("innermost") for f in s["frames"]
    )
    assert "src/api.py" in s["files"] and "UserProfileService.get_profile" in s["symbols"]
    assert s["pull_requests"] == ["https://github.com/acme/api/pull/42"]
    assert disc["comments"] == 3 and disc["tried"] and "restarting" in disc["tried"][0]["text"]
    assert disc["workarounds"] and "disabled the cache" in disc["workarounds"][0]["text"]
    assert disc["cause"] and "soft-deleted" in disc["cause"][0]["text"] and disc["fixes"]
    assert [t["kind"] for t in d["timeline"]] == ["question", "workaround", "fix"] or d["timeline"][2]["kind"] == "fix"
    assert d["participants"] == ["Ana", "Ben", "Cy"]
    assert any("still open" in f for f in d["flags"])  # fix mentioned but ticket open
    assert d["next_steps"][0].startswith("Start at src/user_profile.py:10")
    assert "KeyError" in d["search_terms"]


def test_analysis_flags_missing_information():
    d = ja.analyze(
        issue(description="It is broken sometimes.", comment_items=[], updated="2020-01-01T00:00:00.000+0000")
    )
    assert (
        any("stack trace" in f for f in d["flags"])
        and any("reproduction" in f for f in d["flags"])
        and any("No activity" in f for f in d["flags"])
    )
    assert d["discussion"]["comments"] == 0


def test_investigation_query_carries_the_distilled_signals():
    i = issue(comment_items=COMMENTS)
    q = ja.investigation_query(i, ja.analyze(i))
    assert (
        "Errors: KeyError" in q and "Proposed cause" in q and "Already tried" in q and "Components: user-service" in q
    )


@pytest.fixture
def rich_jira(monkeypatch):
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/issue/BUG-7"):
            return httpx.Response(
                200,
                json={
                    "key": "BUG-7",
                    "fields": {
                        "summary": "Profile endpoint returns 500",
                        "description": "Happens when deleted.\n" + TRACE,
                        "status": {"name": "Open"},
                        "issuetype": {"name": "Bug"},
                        "priority": {"name": "High"},
                        "labels": ["prod"],
                        "components": [{"name": "user-service"}],
                        "reporter": {"displayName": "Ana"},
                        "created": "2026-09-01T10:00:00.000+0000",
                        "updated": "2026-09-02T10:00:00.000+0000",
                        "comment": {
                            "comments": [
                                {"author": {"displayName": c["author"]}, "created": c["created"], "body": c["body"]}
                                for c in COMMENTS
                            ]
                        },
                    },
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(jira_api, "_transport", httpx.MockTransport(handler))


def test_detail_and_analyze_endpoints(client, auth, demo_repo, rich_jira):
    configure_jira(client, auth)
    d = client.get("/api/jira/issues/BUG-7", headers=auth).json()
    assert (
        d["reporter"] == "Ana"
        and len(d["comments"]) == 3
        and d["comments"][2]["author"] == "Cy"
        and d["labels"] == ["prod"]
    )
    a = client.post("/api/jira/issues/BUG-7/analyze", headers=auth)
    assert a.status_code == 200
    a = a.json()
    assert "KeyError" in a["signals"]["errors"] and a["discussion"]["cause"] and isinstance(a["related"], list)
    assert client.get("/api/jira/issues/not-a-key", headers=auth).status_code in (404, 502)


# ------------------------------------------------------------------------------------------------ several tickets at once
def _issue_json(
    key, summary, itype="Bug", status=("Open", "To Do"), desc="", comments=(), created="2026-09-01T10:00:00.000+0000"
):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": desc,
            "status": {"name": status[0], "statusCategory": {"name": status[1]}},
            "issuetype": {"name": itype},
            "priority": {"name": "High"},
            "labels": [],
            "components": [{"name": "user-service"}],
            "reporter": {"displayName": "Ana"},
            "assignee": {"displayName": "Ben"},
            "created": created,
            "updated": created,
            "comment": {
                "comments": [{"author": {"displayName": "Cy"}, "created": created, "body": c} for c in comments]
            },
        },
    }


TICKETS = {
    "BUG-7": _issue_json(
        "BUG-7", "Profile endpoint returns 500 for deleted users", desc="Happens when deleted.\n" + TRACE
    ),
    "BUG-8": _issue_json("BUG-8", "Profile endpoint 500 error for deleted users", desc="Same as before.\n" + TRACE),
    "TASK-3": _issue_json("TASK-3", "Add pagination to the invoice export", "Task", ("Done", "Done")),
}


@pytest.fixture
def many_jira(monkeypatch):
    seen = {"search": []}

    def handler(r: httpx.Request) -> httpx.Response:
        p = r.url.path
        if p.endswith("/search/jql") or p.endswith("/search"):
            seen["search"].append((r.url.params.get("jql"), r.url.params.get("maxResults")))
            return httpx.Response(200, json={"issues": list(TICKETS.values())})
        if p.endswith("/issuetype"):
            return httpx.Response(
                200, json=[{"name": "Bug"}, {"name": "Task"}, {"name": "Sub-task", "subtask": True}, {"name": "Epic"}]
            )
        key = p.rsplit("/", 1)[-1]
        return httpx.Response(200, json=TICKETS[key]) if key in TICKETS else httpx.Response(404)

    monkeypatch.setattr(jira_api, "_transport", httpx.MockTransport(handler))
    return seen


def test_issue_list_has_everything_the_list_view_needs_and_asks_for_the_requested_size(client, auth, many_jira):
    configure_jira(client, auth)
    rows = client.get(
        "/api/jira/issues", headers=auth, params={"jql": "project = BUG ORDER BY updated DESC", "limit": 50}
    ).json()
    assert [r["key"] for r in rows] == ["BUG-7", "BUG-8", "TASK-3"]
    assert {
        "status_category",
        "type",
        "priority",
        "assignee",
        "updated",
        "comments",
        "labels",
        "components",
        "investigation_id",
    } <= set(rows[0])
    assert rows[2]["status_category"] == "Done" and rows[0]["assignee"] == "Ben"
    assert many_jira["search"][0] == ("project = BUG ORDER BY updated DESC", "50")
    assert client.get("/api/jira/issues", headers=auth, params={"limit": 500}).status_code == 422


def test_issue_types_come_from_jira_without_sub_tasks(client, auth, many_jira):
    configure_jira(client, auth)
    assert client.get("/api/jira/issue-types", headers=auth).json() == ["Bug", "Epic", "Task"]


def test_several_tickets_are_analysed_together_and_duplicates_are_spotted(client, auth, many_jira):
    configure_jira(client, auth)
    r = client.post("/api/jira/analyze-batch", headers=auth, json={"keys": ["BUG-7", "BUG-8", "TASK-3"]})
    assert r.status_code == 200
    b = r.json()
    assert [t["key"] for t in b["tickets"]] == ["BUG-7", "BUG-8", "TASK-3"] and "KeyError" in b["tickets"][0][
        "signals"
    ]["errors"]
    c = b["common"]
    assert {"name": "KeyError", "keys": ["BUG-7", "BUG-8"]} in c["errors"]
    assert any(f["name"] == "src/user_profile.py" and f["keys"] == ["BUG-7", "BUG-8"] for f in c["files"])
    assert c["possible_duplicates"] and c["possible_duplicates"][0]["keys"] == ["BUG-7", "BUG-8"]
    assert c["statuses"] == {"Open": 2, "Done": 1} and c["priorities"] == {"High": 3}
    assert isinstance(b["related"], list)
    assert client.post("/api/jira/analyze-batch", headers=auth, json={"keys": []}).status_code == 422
    assert (
        client.post("/api/jira/analyze-batch", headers=auth, json={"keys": [f"BUG-{i}" for i in range(11)]}).status_code
        == 422
    )
    assert client.post("/api/jira/analyze-batch", headers=auth, json={"keys": ["NOPE-1"]}).status_code == 502


def test_investigating_several_tickets_starts_each_once(client, auth, demo_repo, many_jira):
    from tests.test_api import add_repo

    configure_jira(client, auth)
    repo = add_repo(client, auth, demo_repo[0])
    r = client.post(
        "/api/jira/investigate-batch",
        headers=auth,
        json={"keys": ["BUG-7", "BUG-8", "BUG-7"], "repo_ids": [repo["id"]]},
    )
    assert r.status_code == 202
    started = r.json()["started"]
    assert [s["issue_key"] for s in started] == ["BUG-7", "BUG-8"] and r.json()["existing"] == []
    inv = client.get(f"/api/investigations/{started[0]['investigation_id']}", headers=auth).json()
    assert inv["jira_issue_key"] == "BUG-7" and inv["status"] == "completed"
    again = client.post(
        "/api/jira/investigate-batch", headers=auth, json={"keys": ["BUG-7", "TASK-3"], "repo_ids": [repo["id"]]}
    ).json()
    assert [s["issue_key"] for s in again["started"]] == ["TASK-3"] and again["existing"][0]["issue_key"] == "BUG-7"


def test_viewers_may_read_and_analyse_but_not_start_investigations(client, auth, many_jira):
    configure_jira(client, auth)
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"}
    )
    v = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()[
            "access_token"
        ]
    }
    assert client.post("/api/jira/analyze-batch", headers=v, json={"keys": ["BUG-7"]}).status_code in (200, 403)
    assert client.post("/api/jira/investigate-batch", headers=v, json={"keys": ["BUG-7"]}).status_code == 403
