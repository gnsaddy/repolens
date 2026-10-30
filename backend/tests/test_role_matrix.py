"""Every operation, called as viewer, member and admin, with a request that is valid enough to reach the permission check.

The lowest role that gets past the permission check is compared with the lowest role the code says it needs
(`authorize(db, p, "<action>")` through `ACTION_MIN_ROLE`, or `require_role(...)`). Run it with `-s` to print the whole table."""

import inspect
import re
import subprocess

import pytest
from fastapi.routing import APIRoute

from repolens.services.policy import ACTION_MIN_ROLE
from tests.test_api import add_repo

ROLES = ("viewer", "member", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
PUBLIC_PREFIXES = (
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/reset",
    "/api/auth/sso/",
    "/api/webhooks/",
    "/scim/",
)
SKIP = {  # not meaningful to call blindly: long-running, or they end the session the sweep is using
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/logout-others"),
    ("POST", "/api/auth/password"),
}


def resolve(spec, schema):
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
    return schema


def make(spec, schema, name="", depth=0):
    """A small value that satisfies the schema; a property called `role` asks for admin, the worst case."""
    schema = resolve(spec, schema)
    if "anyOf" in schema or "oneOf" in schema:
        options = [o for o in schema.get("anyOf", schema.get("oneOf", [])) if o.get("type") != "null"]
        return make(spec, options[0], name, depth) if options else None
    if "enum" in schema:
        return "admin" if name == "role" and "admin" in schema["enum"] else schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if depth > 4:
            return {}
        props = schema.get("properties", {})
        keep = set(schema.get("required", []))
        return {k: make(spec, v, k, depth + 1) for k, v in props.items() if k in keep}
    if kind == "array":
        return [make(spec, schema["items"], name, depth + 1)] if depth < 3 and "items" in schema else []
    if kind == "integer":
        return max(schema.get("minimum", 1), 1)
    if kind == "number":
        return 1
    if kind == "boolean":
        return False
    low = name.lower()
    if "password" in low or low == "token":
        return "viewer-pass-123"
    if "email" in low:
        return "q@acme.io"
    if "url" in low or "base" in low:
        return "https://example.com"
    if low == "role":
        return "admin"
    return "x" * max(schema.get("minLength", 1), 1)


def route_table(app):
    """{(METHOD, path): route} for every API route, including those FastAPI keeps inside included routers."""
    found = {}
    for r in app.routes:
        for e in r.effective_route_contexts() if hasattr(r, "effective_route_contexts") else [r]:
            if isinstance(e, APIRoute) or hasattr(e, "endpoint"):
                for m in e.methods:
                    found[(m, e.path)] = e
    return found


def operations(client):
    spec = client.app.openapi()
    routes = route_table(client.app)
    out = []
    for path, item in sorted(spec["paths"].items()):
        for method, op in item.items():
            m = method.upper()
            if (
                m not in ("GET", "POST", "PUT", "PATCH", "DELETE")
                or path.startswith(PUBLIC_PREFIXES)
                or (m, path) in SKIP
            ):
                continue
            out.append((m, path, op, routes.get((m, path))))
    return spec, out


def intended(route):
    """(lowest role the code says it needs, how we know). None means any signed-in person."""
    if route is None:
        return None, "unknown"
    gates = []
    for d in route.dependant.dependencies:
        if getattr(d.call, "__qualname__", "") == "require_role.<locals>.dep":
            gates.append(d.call.__closure__[0].cell_contents)
    why = "require_role" if gates else ""
    actions = re.findall(r'authorize\(\s*db,\s*p,\s*"([\w.]+)"', inspect.getsource(route.endpoint))
    gates += [ACTION_MIN_ROLE[a] for a in actions if a in ACTION_MIN_ROLE]
    if actions:
        why = "authorize " + ", ".join(sorted(set(actions)))
    return (min(gates, key=RANK.get) if gates else None), why or "no check in the handler"


@pytest.fixture
def people(client, auth, tmp_path):
    heads = {"admin": auth}
    for role in ("viewer", "member"):
        client.post(
            "/api/auth/users",
            headers=auth,
            json={"email": f"{role}@acme.io", "password": "role-pass-1234", "role": role},
        )
        token = client.post("/api/auth/login", json={"email": f"{role}@acme.io", "password": "role-pass-1234"}).json()[
            "access_token"
        ]
        heads[role] = {"Authorization": f"Bearer {token}"}
    d = tmp_path / "svc"
    d.mkdir()
    (d / "auth.py").write_text("def create_user():\n    return 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(d), *cmd], check=True)
    repo = add_repo(client, auth, str(d), name="svc")
    return heads, {"repo_id": repo["id"], "id": repo["id"]}


def call(client, spec, m, path, op, headers, ids):
    url = re.sub(r"\{(\w+)\}", lambda g: ids.get(g.group(1), "x"), path)
    query = {
        p["name"]: make(spec, p.get("schema", {}), p["name"])
        for p in op.get("parameters", [])
        if p["in"] == "query" and p.get("required")
    }
    body = None
    content = op.get("requestBody", {}).get("content", {})
    if "application/json" in content:
        body = make(spec, content["application/json"]["schema"])
    r = client.request(m, url, headers=headers, params=query, json=body)
    return r.status_code


def sweep(client, people):
    heads, ids = people
    spec, ops = operations(client)
    seen: dict[tuple[str, str], dict[str, int]] = {}
    for role in ROLES:  # the least trusted first, so nothing it should not be able to do has changed anything yet
        for m, path, op, _route in ops:
            seen.setdefault((m, path), {})[role] = call(client, spec, m, path, op, heads[role], ids)
    return ops, seen


def lowest_allowed(codes):
    """The lowest role that got a success. Only a 2xx proves the action was allowed: a 404 or 422 can come before the check."""
    for role in ROLES:
        if 200 <= codes[role] < 300:
            return role
    return None


def test_no_operation_fails_with_a_server_error_for_any_role(client, auth, people):
    _ops, seen = sweep(client, people)
    broken = {
        k: v for k, v in seen.items() if any(c >= 500 and c != 502 for c in v.values())
    }  # 502: an upstream that is not configured
    assert not broken, broken


def test_no_role_gets_in_below_the_role_the_code_requires(client, auth, people, capsys):
    ops, seen = sweep(client, people)
    leaks, table = [], []
    for m, path, _op, route in ops:
        want, why = intended(route)
        codes = seen[(m, path)]
        got = lowest_allowed(codes)
        table.append(
            f"{m:6} {path:58} needs={want or 'any':7} ok_for={got or 'nobody':7} "
            f"v={codes['viewer']} m={codes['member']} a={codes['admin']} {why}"
        )
        if want and got and RANK[got] < RANK[want]:
            leaks.append((m, path, want, got, why))
    with capsys.disabled():
        print("\n" + "\n".join(table))
        print("\nLEAKS (a lower role succeeded than the code requires):", leaks)
    assert not leaks, leaks


def test_a_viewer_and_a_member_cannot_change_things_that_really_exist(client, auth, people):
    """The sweep above uses made-up ids, where a 404 can come before the permission check. Here the objects are real."""
    heads, ids = people
    admin, member, viewer = heads["admin"], heads["member"], heads["viewer"]
    inv = client.post(
        "/api/investigations", headers=admin, json={"query": "a bug", "repo_ids": [ids["repo_id"]]}
    ).json()["id"]
    client.post(
        "/api/golden/questions", headers=admin, json={"question": "How does sign in work?", "expect_files": ["auth.py"]}
    )
    qid = client.get("/api/golden", headers=admin).json()["questions"][0]["id"]
    client.post(
        "/api/skills",
        headers=admin,
        json={"title": "House rules", "body": "Always be brief and cite files.", "always": True},
    )
    skill = client.get("/api/skills", headers=admin).json()["items"][0]["id"]
    coll = client.post("/api/knowledge/collections", headers=admin, json={"name": "Runbooks"}).json()["id"]
    people_ids = {u["email"]: u["id"] for u in client.get("/api/auth/users", headers=admin).json()}
    other = people_ids["member@acme.io"]
    # (method, path, body, lowest role allowed)
    cases = [
        ("DELETE", f"/api/investigations/{inv}", None, "member"),
        ("POST", f"/api/investigations/{inv}/rerun", None, "member"),
        ("POST", f"/api/investigations/{inv}/review", {"outcome": "confirmed"}, "member"),
        ("POST", f"/api/investigations/{inv}/fix-proposals", {}, "member"),
        ("DELETE", f"/api/golden/questions/{qid}", None, "member"),
        ("POST", "/api/golden/run", {"kind": "retrieval"}, "member"),
        ("PATCH", f"/api/skills/{skill}", {"title": "Changed"}, "member"),
        ("POST", f"/api/skills/{skill}/review", {"decision": "approve"}, "admin"),
        ("DELETE", f"/api/skills/{skill}", None, "member"),
        ("DELETE", f"/api/knowledge/collections/{coll}", None, "admin"),
        (
            "POST",
            f"/api/knowledge/collections/{coll}/documents",
            {"documents": [{"path": "a.md", "content": "x"}]},
            "member",
        ),
        ("PUT", f"/api/repositories/{ids['repo_id']}/access", {"visibility": "restricted", "user_ids": []}, "admin"),
        ("POST", f"/api/repositories/{ids['repo_id']}/index", None, "member"),
        ("DELETE", f"/api/repositories/{ids['repo_id']}", None, "admin"),
        ("POST", f"/api/auth/users/{other}/reset-link", None, "admin"),
        ("POST", f"/api/auth/users/{other}/reset-mfa", None, "admin"),
        ("PUT", "/api/settings/llm", {"provider": "openai", "model": "x", "api_key": "k" * 12}, "admin"),
        ("PUT", "/api/settings/review", {"enabled": True}, "admin"),
        ("DELETE", "/api/settings/memory", None, "admin"),
    ]
    denied = []
    for role, who in (("viewer", viewer), ("member", member)):
        for m, path, body, lowest in cases:
            if RANK[role] < RANK[lowest]:
                code = client.request(m, path, headers=who, json=body).status_code
                if code != 403:
                    denied.append((role, m, path, code))
    assert not denied, denied
    for path in (f"/api/investigations/{inv}", f"/api/knowledge/collections/{coll}/documents"):  # nothing was changed
        assert client.get(path, headers=admin).status_code == 200


ADMIN_ONLY_READS = {
    "/api/audit",
    "/api/audit/export.csv",
    "/api/audit/facets",
    "/api/auth/people",
    "/api/auth/users",  # emails, roles and whether two-step sign-in is on, for everyone
    "/api/feedback/export",
    "/api/metrics/health",  # background-job state and repository counts
    "/api/metrics/system",
    "/api/repositories/storage",
    "/api/repositories/{repo_id}/access",
    "/api/settings/llm/github-models/catalog",
    "/api/settings/llm/models",
    "/api/settings/scim",
    "/api/settings/sso",
    "/api/settings/sso/users",
    "/api/wiki/confluence/spaces",
    "/api/wiki/confluence/spaces/{key}/pages",
}
MEMBER_ONLY_READS = {"/api/jira/issue-types", "/api/jira/issues", "/api/jira/issues/{key}", "/api/jira/projects"}


def test_which_reads_each_role_has_is_pinned_so_a_new_one_is_a_decision(client, auth, people):
    """A new GET that a viewer can reach must be added to the viewer list on purpose, not by accident."""
    _ops, seen = sweep(client, people)
    gets = {p: c for (m, p), c in seen.items() if m == "GET"}

    def denied(c):
        return c in (401, 403)

    admin_only = {p for p, c in gets.items() if denied(c["viewer"]) and denied(c["member"])}
    member_only = {p for p, c in gets.items() if denied(c["viewer"]) and not denied(c["member"])}
    assert admin_only == ADMIN_ONLY_READS, admin_only ^ ADMIN_ONLY_READS
    assert member_only == MEMBER_ONLY_READS, member_only ^ MEMBER_ONLY_READS
    open_to_viewers = {p for p, c in gets.items() if not denied(c["viewer"])}
    assert open_to_viewers == set(VIEWER_READS.split()), open_to_viewers ^ set(VIEWER_READS.split())
    assert all(not denied(c["admin"]) for c in gets.values())


# What a viewer can read: workspace content, their own things, and settings without secrets.
VIEWER_READS = """
    /api/auth/api-tokens
    /api/auth/me
    /api/auth/sessions
    /api/bitbucket/connection
    /api/bitbucket/repos
    /api/bitbucket/repos/{owner}/{name}/branches
    /api/bitbucket/repos/{owner}/{name}/pulls
    /api/chats
    /api/chats/gaps
    /api/chats/{thread_id}
    /api/dashboard
    /api/dashboard/calibration
    /api/dashboard/insights
    /api/feedback/summary
    /api/fix-proposals/{fix_id}
    /api/fix-proposals/{fix_id}/patch
    /api/github/connection
    /api/github/repos
    /api/github/repos/{owner}/{name}/branches
    /api/github/repos/{owner}/{name}/issues
    /api/github/repos/{owner}/{name}/pulls
    /api/golden
    /api/golden/runs/{rid}
    /api/graph/communities
    /api/groups
    /api/groups/analysis
    /api/groups/export
    /api/groups/changes
    /api/groups/impact
    /api/groups/surface
    /api/groups/code-graph
    /api/groups/{gid}/analysis
    /api/groups/{gid}/overviews
    /api/groups/{gid}/architecture
    /api/graph/nodes
    /api/graph/nodes/{node_id}
    /api/graph/nodes/{node_id}/brief
    /api/graph/overview
    /api/graph/path
    /api/graph/report
    /api/graph/stats
    /api/investigations
    /api/investigations/{inv_id}
    /api/investigations/{inv_id}/events
    /api/investigations/{inv_id}/fix-proposals
    /api/jira/connection
    /api/jira/publish-events
    /api/knowledge/collections/{repo_id}/documents
    /api/mcp
    /api/metrics/overview
    /api/ownership/experts
    /api/ownership/overview
    /api/ownership/path
    /api/repositories
    /api/repositories/branches
    /api/repositories/{repo_id}
    /api/repositories/{repo_id}/blame
    /api/repositories/{repo_id}/commits
    /api/repositories/{repo_id}/file
    /api/repositories/{repo_id}/measure
    /api/repositories/{repo_id}/pulls
    /api/repositories/{repo_id}/runs
    /api/repositories/{repo_id}/tree
    /api/risk
    /api/risk/scan
    /api/risk/{assessment_id}
    /api/search
    /api/settings/llm
    /api/settings/memory
    /api/settings/memory/entries
    /api/settings/models
    /api/settings/outbound
    /api/settings/retention
    /api/settings/retrieval
    /api/skills
    /api/usage/events
    /api/usage/summary
    /api/wiki/atlas
    /api/wiki/concepts
    /api/wiki/concepts/detail
    /api/wiki/confluence/connection
    /api/wiki/connectors
    /api/wiki/drift
    /api/wiki/drift/count
    /api/wiki/health
    /api/wiki/overview
    /api/wiki/page
    /api/wiki/page/source
    /api/wiki/proposals
    /api/wiki/proposals/{proposal_id}
    /api/wiki/search
    /api/wiki/sources
    /api/wiki/tree
"""
