import json

import pytest

from tests.conftest import register
from tests.test_api import add_repo
from tests.test_knowledge import make_collection, upload


def token(client, h, scope="read"):
    return {
        "Authorization": "Bearer "
        + client.post("/api/auth/api-tokens", headers=h, json={"name": "editor", "scope": scope}).json()["token"]
    }


def rpc(client, h, method, params=None, id_=1):
    body = {
        "jsonrpc": "2.0",
        "method": method,
        **({"params": params} if params is not None else {}),
        **({"id": id_} if id_ is not None else {}),
    }
    r = client.post("/api/mcp", headers=h, content=json.dumps(body))
    return r


def call(client, h, tool, **args):
    r = rpc(client, h, "tools/call", {"name": tool, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return "\n".join(c["text"] for c in res["content"]), res.get("isError", False)


@pytest.fixture
def indexed(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    col = make_collection(client, auth, "handbook")
    upload(
        client,
        auth,
        col["id"],
        {
            "runbook/deleted-users.md": "# Deleted users\n\nWhen `UserProfileService.get_profile` returns a default profile the display name is Unknown.\n"
        },
    )
    return repo, col


def test_the_handshake_negotiates_a_version_and_lists_tools_by_what_the_caller_may_do(client, auth):
    h = token(client, auth, "read")
    init = rpc(
        client,
        h,
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    ).json()["result"]
    assert (
        init["protocolVersion"] == "2025-03-26"
        and init["serverInfo"]["name"] == "repolens"
        and "tools" in init["capabilities"]
    )
    assert (
        rpc(client, h, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]["protocolVersion"]
        == "2025-06-18"
    )
    assert rpc(client, h, "notifications/initialized", id_=None).status_code == 202
    assert rpc(client, h, "ping").json()["result"] == {}
    read = {t["name"] for t in rpc(client, h, "tools/list").json()["result"]["tools"]}
    assert {
        "search",
        "ask",
        "symbol_brief",
        "read_wiki_page",
        "change_risk",
        "who_to_ask",
        "get_investigation",
    } <= read and "investigate" not in read
    write = {t["name"] for t in rpc(client, token(client, auth, "write"), "tools/list").json()["result"]["tools"]}
    assert "investigate" in write
    assert all(t["inputSchema"]["type"] == "object" for t in rpc(client, h, "tools/list").json()["result"]["tools"])


def test_it_speaks_json_rpc_correctly(client, auth):
    h = token(client, auth)
    assert rpc(client, h, "nope/method").json()["error"]["code"] == -32601
    assert client.post("/api/mcp", headers=h, content="{not json").status_code == 400
    assert (
        client.post("/api/mcp", headers=h, content=json.dumps({"id": 1, "method": "ping"})).json()["error"]["code"]
        == -32600
    )
    batch = client.post(
        "/api/mcp",
        headers=h,
        content=json.dumps(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ]
        ),
    )
    assert [r["id"] for r in batch.json()] == [1, 2]
    assert client.get("/api/mcp", headers=h).status_code == 405


def test_it_needs_a_valid_credential(client, auth):
    assert rpc(client, {}, "ping").status_code == 401
    assert rpc(client, {"Authorization": "Bearer rlp_nonsense"}, "ping").status_code == 401
    assert rpc(client, auth, "ping").status_code == 200  # a normal sign-in works too


def test_search_ask_and_wiki_tools_return_cited_evidence(client, auth, indexed):
    h = token(client, auth)
    repo, col = indexed
    text, err = call(client, h, "search", query="get_profile deleted users unknown profile", limit=5)
    assert not err and "get_profile" in text and ("docs" in text or "handbook" in text or "runbook" in text)
    docs, _ = call(client, h, "search", query="deleted users display name", kind="doc")
    assert "Deleted users" in docs
    text, err = call(client, h, "ask", question="What happens to the display name for deleted users?")
    assert not err and "Unknown" in text and "Sources:" in text and "No language model is connected" in text
    text, _ = call(client, h, "read_wiki_page", repo_id=col["id"], path="runbook/deleted-users.md")
    assert text.startswith("# Deleted users") and "Unknown" in text
    repos, _ = call(client, h, "list_repositories")
    assert repo["name"] in repos and "handbook" in repos


def test_symbol_tools_and_who_to_ask_and_risk(client, auth, indexed):
    h = token(client, auth)
    repo, _ = indexed
    found, err = call(client, h, "find_symbol", name="get_profile", type="function")
    assert not err and "UserProfileService.get_profile" in found
    node_id = found.split("node_id ")[1].split(",")[0]
    brief, _ = call(client, h, "symbol_brief", node_id=node_id)
    assert "UserProfileService.get_profile" in brief and "Used by" in brief and "Depends on" in brief
    who, _ = call(client, h, "who_to_ask", repo_id=repo["id"], paths=["src/user_profile.py"])
    assert who and "commits" in who
    sha = client.get(f"/api/repositories/{repo['id']}", headers=auth).json().get("indexed_sha") or repo.get(
        "indexed_sha"
    )
    risk, _ = call(client, h, "change_risk", repo_id=repo["id"], sha=sha)
    assert risk.startswith("Risk ") and "/100" in risk
    assert "Give a sha" in call(client, h, "change_risk", repo_id=repo["id"])[0]


def test_investigations_can_be_started_with_a_write_token_and_read_back(client, auth, indexed):
    repo, _ = indexed
    text, err = call(
        client,
        token(client, auth, "write"),
        "investigate",
        description="KeyError in get_profile for deleted users",
        repo_ids=[repo["id"]],
    )
    assert not err and text.startswith("Started investigation ")
    inv_id = text.split()[2].rstrip(".")
    got, _ = call(client, token(client, auth, "read"), "get_investigation", id=inv_id)
    assert f"Investigation {inv_id}" in got and "Status: completed" in got and "Root cause" in got


def test_a_read_token_cannot_start_work_and_errors_are_reported_not_raised(client, auth):
    h = token(client, auth, "read")
    text, err = call(client, h, "investigate", description="a bug that needs investigating")
    assert err and "denied" in text.lower()
    assert call(client, h, "no_such_tool")[1] is True
    text, err = call(client, h, "search")
    assert err and "Missing required argument" in text
    text, err = call(client, h, "get_investigation", id="does-not-exist")
    assert err and "not found" in text.lower()


def test_callers_only_see_what_their_own_access_allows(client, auth, indexed):
    repo, col = indexed
    client.put(f"/api/repositories/{col['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []})
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    m = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
            "access_token"
        ]
    }
    mt = token(client, m)
    repos, _ = call(client, mt, "list_repositories")
    assert "handbook" not in repos and repo["name"] in repos
    text, err = call(client, mt, "read_wiki_page", repo_id=col["id"], path="runbook/deleted-users.md")
    assert err and "not found" in text.lower()
    assert (
        "runbook/deleted-users.md" not in call(client, mt, "search", query="deleted users display name", kind="doc")[0]
    )  # the restricted collection stays hidden
    other = token(client, register(client, "z@other.io", "Z"))
    assert "No repositories" in call(client, other, "list_repositories")[0]


def test_tool_calls_are_audited_without_what_was_asked(client, auth, session):
    from repolens.models import AuditEvent

    call(client, token(client, auth), "search", query="very private question about salaries")
    rows = session.query(AuditEvent).filter_by(action="mcp.read").all()
    assert rows and rows[-1].target == "search" and "salaries" not in str(rows[-1].detail) + rows[-1].target
