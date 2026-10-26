from repolens.models import KGEdge, KGNode, Organization, Repository
from repolens.services import graph_insights as gi
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_mcp import call, token


# ---------------------------------------------------------------- pure functions
def test_trust_separates_facts_from_inference_and_weak_guesses():
    for prov in ("static_analysis", "doc_link", "git", "github"):
        assert gi.trust(prov) == "fact"
    assert gi.trust("semantic", 0.9) == "inferred" and gi.trust("doc_mention", 0.5) == "inferred"
    assert gi.trust("semantic", 0.3) == "uncertain"


def test_communities_find_the_two_clusters_and_leave_loose_nodes_out():
    ids = [f"a{i}" for i in range(5)] + [f"b{i}" for i in range(4)] + ["lonely", "pair1", "pair2"]
    edges = [(f"a{i}", f"a{j}", 1.0) for i in range(5) for j in range(i + 1, 5)]
    edges += [(f"b{i}", f"b{j}", 1.0) for i in range(4) for j in range(i + 1, 4)]
    edges += [("a0", "b0", 0.2), ("pair1", "pair2", 1.0)]
    m = gi.label_propagation(ids, edges)
    assert len({m[f"a{i}"] for i in range(5)}) == 1 and len({m[f"b{i}"] for i in range(4)}) == 1
    assert m["a0"] != m["b0"] and m["a0"] == 0  # the larger cluster is community 0
    assert "lonely" not in m and "pair1" not in m  # fewer than three members: not a community
    assert m == gi.label_propagation(reversed(ids), edges)  # the same graph always gives the same answer


# ---------------------------------------------------------------- on a stored graph
def make_graph(session):
    org = Organization(name="o")
    session.add(org)
    session.flush()
    repo = Repository(org_id=org.id, name="svc", source="local", url="x", status="ready", indexed_sha="s1")
    session.add(repo)
    session.flush()

    def node(i, name, path, type_="function", degree=1):
        n = KGNode(id=i, org_id=org.id, repo_id=repo.id, type=type_, name=name, path=path, degree=degree)
        session.add(n)
        return n

    for i in range(4):
        node(f"b{i}", f"bill{i}", "svc/billing/pay.py", degree=3 if i == 0 else 1)
        node(f"u{i}", f"user{i}", "svc/users/profile.py", degree=2)
    node("hub", "Core", "svc/core.py", "class", degree=9)
    k = [0]

    def edge(a, b, rel="CALLS", prov="static_analysis", w=1.0):
        k[0] += 1
        session.add(
            KGEdge(
                id=f"e{k[0]}", org_id=org.id, repo_id=repo.id, src_id=a, dst_id=b, rel=rel, provenance=prov, weight=w
            )
        )

    for grp in ("b", "u"):
        for i in range(4):
            for j in range(i + 1, 4):
                edge(f"{grp}{i}", f"{grp}{j}")
    edge("b0", "u0", "RELATED_TO", "semantic", 0.62)
    session.commit()
    return org, repo


def test_report_names_areas_hubs_surprising_links_and_questions(session):
    org, repo = make_graph(session)
    r = gi.report(session, repo)
    assert r["stats"]["communities"] == 2 and [c["size"] for c in r["communities"]] == [4, 4]
    assert {c["label"] for c in r["communities"]} == {"svc/billing", "svc/users"}
    assert r["hubs"][0]["name"] == "Core" and r["hubs"][0]["degree"] == 9
    s = r["surprising"]
    assert len(s) == 1 and {s[0]["source"]["id"], s[0]["target"]["id"]} == {"b0", "u0"} and s[0]["trust"] == "inferred"
    assert r["questions"] and "Core" in r["questions"][0]
    md = r["markdown"]
    assert (
        md.startswith("# Start here: svc")
        and "## Hubs" in md
        and "## Surprising connections" in md
        and "inferred" in md
    )


def test_the_analysis_is_cached_until_the_repository_is_indexed_again(session):
    org, repo = make_graph(session)
    assert gi.analyse(session, repo) is gi.analyse(session, repo)
    repo.indexed_sha = "s2"
    assert gi.analyse(session, repo)["nodes"] == 9


# ---------------------------------------------------------------- API and editor tools
def test_api_marks_edges_with_trust_and_nodes_with_a_community(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    ov = client.get("/api/graph/overview", headers=auth, params={"repo_id": repo["id"]}).json()
    assert ov["edges"] and {e["trust"] for e in ov["edges"]} <= {"fact", "inferred", "uncertain"}
    assert all("community" in n for n in ov["nodes"])
    rep = client.get("/api/graph/report", headers=auth, params={"repo_id": repo["id"]}).json()
    assert rep["repo"]["name"] == "user-service" and rep["markdown"].startswith("# Start here")
    assert (
        rep["hubs"]
        and client.get("/api/graph/communities", headers=auth, params={"repo_id": repo["id"]}).json()["communities"]
        is not None
    )


def test_reports_respect_workspaces_and_sign_in(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    other = register(client, "z@other.io", "Other")
    for path in ("report", "communities"):
        assert client.get(f"/api/graph/{path}", headers=other, params={"repo_id": repo["id"]}).status_code == 404
        assert client.get(f"/api/graph/{path}", params={"repo_id": repo["id"]}).status_code == 401


def test_editors_can_read_the_map_neighbours_and_paths(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    h = token(client, auth)
    text, err = call(client, h, "graph_overview", repo_id=repo["id"])
    assert not err and text.startswith("# Start here: user-service")
    syms, _ = call(client, h, "find_symbol", name="get_profile")
    node_id = syms.split("node_id ")[1].split(",")[0]
    neigh, err = call(client, h, "graph_neighbors", node_id=node_id)
    assert not err and "connections" in neigh and ("fact" in neigh or "inferred" in neigh or "No connections" in neigh)
    api_syms, _ = call(client, h, "find_symbol", name="handle_profile_request")
    other_id = api_syms.split("node_id ")[1].split(",")[0]
    path, err = call(client, h, "graph_path", from_id=node_id, to_id=other_id)
    assert not err and ("get_profile" in path or "No connection" in path)
