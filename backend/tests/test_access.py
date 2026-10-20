import pytest

from repolens.models import AuditEvent
from tests.test_api import add_repo
from tests.test_risk import commit, git

MARKER = "ZEBRAFISH"
QUERY = f"{MARKER} discount logic secret_pricing_rule pricing"


def make_secret(tmp_path):
    p = tmp_path / "secret-repo"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(
        p,
        {
            "sec/__init__.py": "",
            "sec/rules.py": f'def secret_pricing_rule(cart):\n    """{MARKER} discount logic."""\n    return shared_util(sum(cart)) * 0.9\n',
        },
        "feat: pricing rules",
        "2026-01-05T10:00:00Z",
    )
    commit(
        p,
        {
            "sec/rules.py": f'def secret_pricing_rule(cart):\n    """{MARKER} discount logic."""\n    return shared_util(sum(cart)) * 0.8\n'
        },
        "fix: wrong discount factor",
        "2026-02-01T10:00:00Z",
    )
    return p


def make_public(tmp_path):
    p = tmp_path / "public-repo"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(
        p,
        {
            "pub/__init__.py": "",
            "pub/util.py": "def shared_util(x):\n    return x + 1\n",
            "pub/service.py": "def checkout(cart):\n    return secret_pricing_rule(cart)\n",
            "docs/pricing.md": "# Pricing\n\nCarts are priced by `secret_pricing_rule` after `shared_util` runs.\n",
        },
        "feat: public service",
        "2026-01-06T10:00:00Z",
    )
    commit(
        p, {"pub/util.py": "def shared_util(x):\n    return x + 2\n"}, "refactor: tweak util", "2026-03-01T10:00:00Z"
    )
    return p


@pytest.fixture
def world(client, auth, tmp_path):
    def user(email, role):
        client.post("/api/auth/users", headers=auth, json={"email": email, "password": "password-1234", "role": role})
        r = client.post("/api/auth/login", json={"email": email, "password": "password-1234"}).json()
        return {"Authorization": f"Bearer {r['access_token']}"}, r["user"]["id"]

    m1, m1_id = user("m1@acme.io", "member")
    m2, m2_id = user("m2@acme.io", "member")
    v, v_id = user("v@acme.io", "viewer")
    secret = add_repo(client, auth, make_secret(tmp_path), name="secret-repo")
    public = add_repo(client, auth, make_public(tmp_path), name="public-repo")
    client.post(f"/api/repositories/{secret['id']}/index", headers=auth)  # relink cross-repo edges both ways
    client.post(f"/api/repositories/{public['id']}/index", headers=auth)
    r = client.put(
        f"/api/repositories/{secret['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": [m1_id]}
    )
    assert r.status_code == 200, r.text
    return {
        "admin": auth,
        "m1": m1,
        "m2": m2,
        "v": v,
        "ids": {"m1": m1_id, "m2": m2_id, "v": v_id},
        "secret": secret["id"],
        "public": public["id"],
    }


def derived(inv: dict) -> str:
    """Everything an investigation *derived* from repositories (the user's own query text is excluded)."""
    return str(
        {
            k: inv[k]
            for k in ("hypotheses", "report_md", "evidence", "root_cause", "suggested_fix", "timeline", "verification")
        }
    )


def names(client, h):
    return {r["name"] for r in client.get("/api/repositories", headers=h).json()}


# ------------------------------------------------------------------ the repository itself
def test_restricted_repo_is_invisible_without_a_grant(client, world):
    for who in ("m2", "v"):
        h = world[who]
        assert names(client, h) == {"public-repo"}
        sid = world["secret"]
        for path in ("", "/tree", "/commits", "/runs", "/pulls", "/access"):
            assert client.get(f"/api/repositories/{sid}{path}", headers=h).status_code in (403, 404), (who, path)
        assert (
            client.get(f"/api/repositories/{sid}/file", headers=h, params={"path": "sec/rules.py"}).status_code == 404
        )
        assert (
            client.get(f"/api/repositories/{sid}/blame", headers=h, params={"path": "sec/rules.py"}).status_code == 404
        )
        assert client.post(f"/api/repositories/{sid}/index", headers=h).status_code in (403, 404)
        assert client.delete(f"/api/repositories/{sid}", headers=h).status_code in (403, 404)


def test_granted_users_and_admins_see_it(client, world):
    assert names(client, world["m1"]) == {"public-repo", "secret-repo"} == names(client, world["admin"])
    assert client.get(f"/api/repositories/{world['secret']}", headers=world["m1"]).status_code == 200


def test_search_never_returns_restricted_code(client, world):
    for h in (world["m2"], world["v"]):
        assert client.get("/api/search", headers=h, params={"q": MARKER}).json() == []
        assert client.get("/api/search", headers=h, params={"q": MARKER, "repo_id": world["secret"]}).status_code == 404
    assert any(
        MARKER in x["snippet"] for x in client.get("/api/search", headers=world["m1"], params={"q": MARKER}).json()
    )
    assert client.get("/api/search", headers=world["admin"], params={"q": MARKER}).json()


# ------------------------------------------------------------------ knowledge + graph (indirect leaks)
def test_knowledge_search_and_ask_hide_restricted_content_and_links(client, world):
    for who in ("m2", "v"):
        hits = client.post("/api/knowledge/search", headers=world[who], json={"query": QUERY}).json()
        blob = str(hits)
        assert MARKER not in blob and "secret-repo" not in blob and "sec/rules.py" not in blob
        # the public doc DOES mention secret_pricing_rule, but that connection must not reveal the restricted node
        doc = next(h for h in hits if h["file"] == "docs/pricing.md")
        assert all("secret_pricing_rule" not in (c["node"]["qualname"] or "") for c in doc["connections"])
        ask = client.post("/api/knowledge/ask", headers=world[who], json={"query": QUERY}).json()
        out = str({k: ask[k] for k in ("sources", "answer", "connections")})
        assert MARKER not in out and "secret-repo" not in out and "sec/rules.py" not in out
    m1 = client.post("/api/knowledge/search", headers=world["m1"], json={"query": QUERY}).json()
    doc = next(h for h in m1 if h["file"] == "docs/pricing.md")
    assert any(
        c["node"]["qualname"] == "secret_pricing_rule" for c in doc["connections"]
    )  # the granted user does see it
    assert any(h["file"] == "sec/rules.py" for h in m1)


def test_graph_endpoints_hide_restricted_nodes_edges_and_paths(client, world):
    adm = client.get("/api/graph/stats", headers=world["admin"]).json()
    m2 = client.get("/api/graph/stats", headers=world["m2"]).json()
    assert 0 < m2["total_nodes"] < adm["total_nodes"] and m2["total_edges"] < adm["total_edges"]
    ov = client.get("/api/graph/overview", headers=world["m2"], params={"limit": 200}).json()
    assert "secret_pricing_rule" not in str(ov) and "secret-repo" not in str(ov)
    names_in_graph = {n["qualname"] or n["name"] for n in ov["nodes"]}
    assert {"checkout", "shared_util"} <= names_in_graph
    assert client.get("/api/graph/nodes", headers=world["m2"], params={"q": "secret_pricing"}).json() == []
    # admin can find the secret node; m2 cannot open it by id, see it as a neighbour, or route a path through it
    sec_node = client.get(
        "/api/graph/nodes", headers=world["admin"], params={"q": "secret_pricing", "type": "function"}
    ).json()[0]
    pub_node = client.get(
        "/api/graph/nodes", headers=world["admin"], params={"q": "checkout", "type": "function"}
    ).json()[0]
    assert client.get(f"/api/graph/nodes/{sec_node['id']}", headers=world["m2"]).status_code == 404
    detail = client.get(f"/api/graph/nodes/{pub_node['id']}", headers=world["m2"], params={"depth": 2}).json()
    exposed = [n["qualname"] for n in detail["nodes"]] + [c["node"]["qualname"] for c in detail["connections"]]
    assert "secret_pricing_rule" not in exposed and all(n["repo"] != "secret-repo" for n in detail["nodes"])
    assert not any(e["source"] == sec_node["id"] or e["target"] == sec_node["id"] for e in detail["edges"])
    admin_detail = client.get(f"/api/graph/nodes/{pub_node['id']}", headers=world["admin"]).json()
    assert any(c["node"]["qualname"] == "secret_pricing_rule" for c in admin_detail["connections"])  # exists for others
    assert (
        client.get(
            "/api/graph/path", headers=world["m2"], params={"src": pub_node["id"], "dst": sec_node["id"]}
        ).status_code
        == 404
    )
    shared = client.get(
        "/api/graph/nodes", headers=world["admin"], params={"q": "shared_util", "type": "function"}
    ).json()[0]
    path = client.get(
        "/api/graph/path", headers=world["m2"], params={"src": pub_node["id"], "dst": shared["id"]}
    ).json()
    assert sec_node["id"] not in str(path)  # a route may not hop through the hidden function


# ------------------------------------------------------------------ investigations and memory
def run_inv(client, h, query=QUERY, repo_ids=None):
    r = client.post("/api/investigations", headers=h, json={"query": query, "repo_ids": repo_ids or []})
    assert r.status_code == 202, r.text
    return client.get(f"/api/investigations/{r.json()['id']}", headers=h).json()


def test_unscoped_investigation_by_a_restricted_user_never_touches_hidden_repos(client, world):
    inv = run_inv(client, world["m2"])
    assert inv["status"] == "completed"
    assert MARKER not in derived(inv) and "secret-repo" not in derived(inv) and "sec/rules.py" not in derived(inv)
    assert (
        client.post(
            "/api/investigations", headers=world["m2"], json={"query": QUERY, "repo_ids": [world["secret"]]}
        ).status_code
        == 404
    )


def test_investigations_that_touched_hidden_repos_are_hidden_from_others(client, world):
    adm = run_inv(client, world["admin"])  # unscoped admin run: its evidence includes the restricted repo
    assert "secret-repo" in str(adm["hypotheses"])
    m1 = run_inv(client, world["m1"])
    ids = {i["id"] for i in client.get("/api/investigations", headers=world["m2"]).json()}
    assert adm["id"] not in ids and m1["id"] not in ids
    for who in ("m2", "v"):
        for path in ("", "/events"):
            assert client.get(f"/api/investigations/{adm['id']}{path}", headers=world[who]).status_code == 404
        assert client.post(
            f"/api/investigations/{m1['id']}/review", headers=world[who], json={"outcome": "confirmed"}
        ).status_code in (403, 404)
        assert client.post(
            "/api/jira/publish", headers=world[who], json={"investigation_id": adm["id"], "approved": True}
        ).status_code in (403, 404)
    assert adm["id"] in {i["id"] for i in client.get("/api/investigations", headers=world["m1"]).json()}


def test_memory_derived_from_restricted_repos_is_not_recalled_by_others(client, world):
    first = run_inv(client, world["admin"])  # writes an incident memory scoped to both repositories
    assert "secret-repo" in str(first["hypotheses"])
    again_admin = run_inv(client, world["admin"])
    assert again_admin["evidence"]["memory"], "unrestricted users recall it"
    for who in ("m2", "v"):
        if who == "v":
            continue  # viewers cannot start investigations
        mine = run_inv(client, world[who])
        assert mine["evidence"]["memory"] == [] and MARKER not in derived(mine)
    m1 = run_inv(client, world["m1"])
    assert m1["evidence"]["memory"], "a user with access to every source repository does recall it"


def test_revoking_a_grant_takes_effect_immediately(client, world):
    sid, m1_id = world["secret"], world["ids"]["m1"]
    mine = run_inv(client, world["m1"])
    assert "secret-repo" in str(mine["hypotheses"])
    assert client.get(f"/api/investigations/{mine['id']}", headers=world["m1"]).status_code == 200
    client.put(
        f"/api/repositories/{sid}/access", headers=world["admin"], json={"visibility": "restricted", "user_ids": []}
    )
    assert names(client, world["m1"]) == {"public-repo"}
    assert (
        client.get(f"/api/investigations/{mine['id']}", headers=world["m1"]).status_code == 404
    )  # their own old result too
    assert client.post(
        "/api/knowledge/search", headers=world["m1"], json={"query": QUERY}
    ).json() and MARKER not in str(
        client.post("/api/knowledge/search", headers=world["m1"], json={"query": QUERY}).json()
    )
    client.put(
        f"/api/repositories/{sid}/access",
        headers=world["admin"],
        json={"visibility": "restricted", "user_ids": [m1_id]},
    )
    assert names(client, world["m1"]) == {"public-repo", "secret-repo"}
    client.put(f"/api/repositories/{sid}/access", headers=world["admin"], json={"visibility": "org", "user_ids": []})
    assert names(client, world["m2"]) == {"public-repo", "secret-repo"}  # org-wide again


# ------------------------------------------------------------------ risk, dashboard
def test_risk_respects_scope_for_repos_assessments_and_dependents(client, world):
    sid, pid = world["secret"], world["public"]
    assert (
        client.post(
            "/api/risk/assess", headers=world["m2"], json={"repo_id": sid, "files": [{"path": "sec/rules.py"}]}
        ).status_code
        == 404
    )
    assert client.get("/api/risk/scan", headers=world["m2"], params={"repo_id": sid}).status_code == 404
    # an admin assesses the public util change (by commit, so the changed function is resolved): its dependents
    # include the restricted function in the other repository
    commits = client.get(f"/api/repositories/{pid}/commits", headers=world["admin"]).json()
    sha = next(c["sha"] for c in commits if "tweak util" in c["message"])
    adm = client.post("/api/risk/assess", headers=world["admin"], json={"repo_id": pid, "sha": sha}).json()
    assert any(d["name"] == "secret_pricing_rule" and d["cross_repo"] for d in adm["affected"]["dependents"])
    # the same stored assessment, read by m2, no longer names it; neither does a fresh one
    seen = client.get(f"/api/risk/{adm['id']}", headers=world["m2"]).json()
    assert "secret_pricing_rule" not in str(seen) and "secret-repo" not in str(seen)
    assert seen["affected"]["dependents"] == []  # the only dependent lives in the hidden repository
    fresh = client.post("/api/risk/assess", headers=world["m2"], json={"repo_id": pid, "sha": sha}).json()
    assert "secret_pricing_rule" not in str(fresh) and "secret-repo" not in str(fresh)
    # assessments of the restricted repo are hidden from lists and ids
    sec = client.post(
        "/api/risk/assess", headers=world["admin"], json={"repo_id": sid, "files": [{"path": "sec/rules.py"}]}
    ).json()
    assert client.get(f"/api/risk/{sec['id']}", headers=world["m2"]).status_code == 404
    assert sec["id"] not in {r["id"] for r in client.get("/api/risk", headers=world["m2"]).json()}
    assert sec["id"] in {r["id"] for r in client.get("/api/risk", headers=world["m1"]).json()}


def test_dashboard_counts_only_visible_things(client, world):
    adm_inv = run_inv(client, world["admin"])
    adm = client.get("/api/dashboard", headers=world["admin"]).json()
    m2 = client.get("/api/dashboard", headers=world["m2"]).json()
    assert adm["repositories"] == 2 and m2["repositories"] == 1
    assert adm["investigations"] == 1 and m2["investigations"] == 0
    assert adm_inv["title"] not in str(m2) and "secret-repo" not in str(m2)


def test_restricted_document_collections(client, world):
    col = client.post("/api/knowledge/collections", headers=world["admin"], json={"name": "hr-docs"}).json()
    client.post(
        f"/api/knowledge/collections/{col['id']}/documents",
        headers=world["admin"],
        json={"documents": [{"path": "salaries.md", "content": "# Salaries\n\nThe PLATYPUS band tops out at 90k.\n"}]},
    )
    client.put(
        f"/api/repositories/{col['id']}/access",
        headers=world["admin"],
        json={"visibility": "restricted", "user_ids": [world["ids"]["m1"]]},
    )
    assert client.get(f"/api/knowledge/collections/{col['id']}/documents", headers=world["m2"]).status_code == 404
    assert (
        client.post(
            f"/api/knowledge/collections/{col['id']}/documents",
            headers=world["m2"],
            json={"documents": [{"path": "x.md", "content": "# x"}]},
        ).status_code
        == 404
    )
    for who, expect in (("m2", False), ("m1", True)):
        ask = client.post("/api/knowledge/ask", headers=world[who], json={"query": "PLATYPUS salary band"}).json()
        assert ("PLATYPUS" in str({k: ask[k] for k in ("sources", "answer")})) is expect, who


# ------------------------------------------------------------------ managing access
def test_only_admins_manage_access_and_inputs_are_validated(client, world, session):
    sid = world["secret"]
    for who in ("m1", "m2", "v"):
        assert client.put(
            f"/api/repositories/{sid}/access", headers=world[who], json={"visibility": "org", "user_ids": []}
        ).status_code in (403, 404)
        assert client.get(f"/api/repositories/{sid}/access", headers=world[who]).status_code in (403, 404)
    admin = world["admin"]
    assert (
        client.put(
            f"/api/repositories/{sid}/access", headers=admin, json={"visibility": "secret", "user_ids": []}
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/repositories/{sid}/access", headers=admin, json={"visibility": "restricted", "user_ids": ["nope"]}
        ).status_code
        == 400
    )
    got = client.get(f"/api/repositories/{sid}/access", headers=admin).json()
    assert got["visibility"] == "restricted" and [u["email"] for u in got["users"]] == ["m1@acme.io"]
    assert session.query(AuditEvent).filter_by(action="repo.access.manage", allowed=True).count() >= 2
    assert session.query(AuditEvent).filter_by(action="repo.access.manage", allowed=False).count() >= 1


def test_users_from_another_organisation_cannot_be_granted(client, world):
    from tests.conftest import register

    other = register(client, "x@other.io", "Other")
    other_id = client.get("/api/auth/me", headers=other).json()["id"]
    r = client.put(
        f"/api/repositories/{world['secret']}/access",
        headers=world["admin"],
        json={"visibility": "restricted", "user_ids": [other_id]},
    )
    assert r.status_code == 400


def test_nothing_changes_when_no_repository_is_restricted(client, auth, tmp_path):
    add_repo(client, auth, make_public(tmp_path), name="public-repo")
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "password-1234", "role": "member"}
    )
    tok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "password-1234"}).json()[
        "access_token"
    ]
    assert names(client, {"Authorization": f"Bearer {tok}"}) == {"public-repo"}


def test_a_member_with_no_accessible_repositories_sees_empty_results_not_everything(client, auth, tmp_path):
    secret = add_repo(client, auth, make_secret(tmp_path), name="secret-repo")
    client.put(
        f"/api/repositories/{secret['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "password-1234", "role": "member"}
    )
    m = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "m@acme.io", "password": "password-1234"}).json()[
            "access_token"
        ]
    }
    assert client.get("/api/repositories", headers=m).json() == []
    assert (
        client.get("/api/search", headers=m, params={"q": MARKER}).json() == []
    )  # an empty scope must not mean "everything"
    assert client.post("/api/knowledge/search", headers=m, json={"query": MARKER}).json() == []
    inv = run_inv(client, m)
    assert inv["status"] in ("completed", "failed") and MARKER not in derived(inv)


def test_crawl_of_read_endpoints_never_mentions_the_restricted_repo(client, world):
    run_inv(client, world["admin"])
    client.post(
        "/api/risk/assess",
        headers=world["admin"],
        json={"repo_id": world["secret"], "files": [{"path": "sec/rules.py"}]},
    )
    h = world["m2"]
    urls = [
        "/api/repositories",
        "/api/dashboard",
        "/api/investigations",
        "/api/risk",
        "/api/graph/stats",
        "/api/graph/overview?limit=300",
        "/api/graph/nodes?q=sec",
        "/api/graph/nodes?q=rules",
        "/api/usage/summary",
        f"/api/search?q={MARKER}",
        "/api/jira/publish-events",
        "/api/auth/users",
    ]
    for url in urls:
        text = client.get(url, headers=h).text
        for needle in ("secret-repo", MARKER, "secret_pricing_rule", "sec/rules.py"):
            assert needle not in text, (url, needle)
