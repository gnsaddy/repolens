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
