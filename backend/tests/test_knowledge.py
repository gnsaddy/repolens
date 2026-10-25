import pytest

from repolens.models import KGEdge, KGNode
from repolens.services.docs import extract_links, extract_mentions, parse_markdown
from repolens.services.graph import resolve_import
from tests.conftest import register
from tests.test_api import add_repo

# ---------------------------------------------------------------- unit: docs parsing
MD = """---
title: Guide
---
Intro with `Foo.bar()` and [other](./other.md#setup) and [[Wiki Page]].

# Install
Run `make run`.
```
# not a heading
```
## Docker
Use `docker compose up`; see `src/api.py` and load_config.

Title
=====
rst section with handle_request
"""


def test_markdown_sections_and_hierarchy():
    chunks = parse_markdown("docs/guide.md", MD)
    assert [c.symbol for c in chunks] == ["Guide", "Install", "Docker", "Title"]
    assert chunks[2].parent == "Install"  # heading path
    assert "# not a heading" in chunks[1].content  # fenced code is not a heading
    assert chunks[1].end_line < chunks[2].start_line


def test_markdown_links_and_mentions():
    chunks = parse_markdown("docs/guide.md", MD)
    assert "./other.md#setup" in chunks[0].imports and "wiki:Wiki Page" in chunks[0].imports
    assert "Foo.bar" in chunks[0].calls
    assert "src/api.py" in chunks[2].calls and "load_config" in chunks[2].calls
    assert "make run" not in chunks[1].calls  # not an identifier
    assert extract_links("[x](https://e.com) ![img](a.png)") == []
    assert "handle_request" in extract_mentions("see handle_request here")


def test_long_section_is_windowed():
    text = "# Big\n" + "\n".join(f"line {i}" for i in range(200))
    chunks = parse_markdown("big.md", text)
    assert len(chunks) > 2 and all(c.symbol == "Big" for c in chunks)
    assert chunks[1].start_line < chunks[0].end_line  # overlap


def test_resolve_python_relative_and_absolute():
    paths = {"src/repository.py", "src/user_profile.py", "pkg/util/helpers.py"}
    assert resolve_import("from .repository import UserRepository", "python", "src/user_profile.py", paths) == {
        "src/repository.py"
    }
    assert resolve_import("import util.helpers", "python", "x.py", paths) == {"pkg/util/helpers.py"}


def test_resolve_js_relative():
    paths = {"web/src/lib/api.ts", "web/src/App.tsx"}
    assert resolve_import("import { api } from './lib/api'", "typescript", "web/src/App.tsx", paths) == {
        "web/src/lib/api.ts"
    }


# ---------------------------------------------------------------- integration: graph from a real repo
def graph(client, auth, **params):
    return client.get("/api/graph/overview", headers=auth, params={"limit": 200, **params}).json()


def edge_set(g):
    names = {n["id"]: n["qualname"] or n["name"] for n in g["nodes"]}
    return {(names[e["source"]], e["rel"], names[e["target"]]) for e in g["edges"]}


@pytest.fixture
def indexed(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    return repo


def test_graph_connects_code_docs_tests_and_history(client, auth, indexed):
    g = graph(client, auth, types="file,doc,section,function,class,commit")
    edges = edge_set(g)
    assert ("src/user_profile.py", "IMPORTS", "src/repository.py") in edges
    assert ("UserProfileService.get_profile", "CALLS", "UserRepository.find_by_id") in edges
    assert ("handle_profile_request", "CALLS", "UserProfileService.get_profile") in edges
    # doc -> code via backticks, doc -> file via links
    assert ("Architecture > Profile lookup", "MENTIONS", "UserProfileService.get_profile") in edges
    assert ("Architecture > Profile lookup", "LINKS_TO", "src/api.py") in edges
    assert ("README.md", "CONTAINS", "user-service") in edges or any(
        r == "LINKS_TO" and s == "user-service" for s, r, t in edges
    )
    # tests and git history
    assert ("tests/test_user_profile.py", "TESTS", "src/user_profile.py") in edges
    assert any(r == "MODIFIES" and t == "src/user_profile.py" for s, r, t in edges)


def test_edges_carry_provenance_and_stats(client, auth, indexed):
    g = graph(client, auth)
    prov = {e["rel"]: e["provenance"] for e in g["edges"]}
    assert prov["CALLS"] == "static_analysis" and prov["MENTIONS"] == "doc_mention"
    stats = client.get("/api/graph/stats", headers=auth).json()
    assert stats["nodes"]["function"] >= 3 and stats["nodes"]["section"] >= 3 and stats["total_edges"] > 10


def test_node_detail_neighbors_and_path(client, auth, indexed):
    nodes = client.get("/api/graph/nodes", headers=auth, params={"q": "get_profile", "type": "function"}).json()
    get_profile = next(n for n in nodes if n["qualname"] == "UserProfileService.get_profile")
    d = client.get(f"/api/graph/nodes/{get_profile['id']}", headers=auth, params={"depth": 1}).json()
    rels = {(c["label"], c["node"]["qualname"]) for c in d["connections"]}
    assert ("calls", "UserRepository.find_by_id") in rels and ("called by", "handle_profile_request") in rels
    assert any(lbl == "mentioned in" for lbl, _ in rels)
    assert "def get_profile" in d["content"]
    # path: test file -> repository file passes through the service
    t = client.get("/api/graph/nodes", headers=auth, params={"q": "test_user_profile.py"}).json()[0]
    r = client.get("/api/graph/nodes", headers=auth, params={"q": "src/repository.py", "type": "file"}).json()[0]
    path = client.get("/api/graph/path", headers=auth, params={"src": t["id"], "dst": r["id"]}).json()
    assert path["found"] and len(path["steps"]) >= 3 and path["steps"][0]["edge"] is None


def test_graph_is_tenant_isolated(client, auth, indexed):
    other = register(client, "x@other.io", "Other")
    node = client.get("/api/graph/nodes", headers=auth, params={"q": "get_profile"}).json()[0]
    assert client.get(f"/api/graph/nodes/{node['id']}", headers=other).status_code == 404
    assert graph(client, other)["nodes"] == []
    assert client.get("/api/graph/stats", headers=other).json()["total_nodes"] == 0


def test_reindex_keeps_node_ids_stable_and_updates_graph(client, auth, demo_repo, indexed):
    before = {n["id"] for n in graph(client, auth)["nodes"]}
    path, _ = demo_repo
    (path / "src" / "extra.py").write_text(
        "from .user_profile import UserProfileService\n\ndef audit(s):\n    return s.get_profile(1)\n"
    )
    import subprocess

    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add audit"], check=True
    )
    client.post(f"/api/repositories/{indexed['id']}/index", headers=auth)
    g = graph(client, auth)
    assert before <= {n["id"] for n in g["nodes"]}  # deterministic ids survive
    assert ("audit", "CALLS", "UserProfileService.get_profile") in edge_set(g)


# ---------------------------------------------------------------- doc collections (uploads)
def make_collection(client, auth, name="handbook"):
    r = client.post("/api/knowledge/collections", headers=auth, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def upload(client, auth, cid, docs):
    return client.post(
        f"/api/knowledge/collections/{cid}/documents",
        headers=auth,
        json={"documents": [{"path": p, "content": c} for p, c in docs.items()]},
    )


DOCS = {
    "runbook/deleted-users.md": "# Deleted users\n\nWhen `UserProfileService.get_profile` returns a default profile the "
    "display name is Unknown. See [architecture](../arch.md).\n",
    "arch.md": "# Architecture\n\nThe profile lookup flows from the handle_profile_request handler into the repository.\n",
}


def test_upload_documents_builds_cross_graph(client, auth, indexed):
    col = make_collection(client, auth)
    assert upload(client, auth, col["id"], DOCS).json() == {"received": 2, "changed": 2, "secrets_redacted": 0}
    repo = client.get(f"/api/repositories/{col['id']}", headers=auth).json()
    assert repo["status"] == "ready" and repo["file_count"] == 2 and repo["source"] == "docs"
    g = graph(client, auth, types="file,doc,section,function,class")
    edges = edge_set(g)
    assert ("Deleted users", "MENTIONS", "UserProfileService.get_profile") in edges  # cross-repo, unique name
    assert ("Deleted users", "LINKS_TO", "arch.md") in edges
    # file viewer serves uploaded docs
    f = client.get(f"/api/repositories/{col['id']}/file", headers=auth, params={"path": "arch.md"}).json()
    assert "profile lookup" in f["content"]


def test_upload_validation(client, auth):
    col = make_collection(client, auth)
    for bad in ("../etc/passwd.md", "notes.exe", "a/../../b.md", "..\\win.md"):
        assert upload(client, auth, col["id"], {bad: "x"}).status_code == 400, bad
    assert upload(client, auth, col["id"], {"a.md": "bin\x00ary"}).status_code == 400
    assert upload(client, auth, col["id"], {"/abs/x.md": "# x"}).status_code == 200  # normalised to relative
    docs = client.get(f"/api/knowledge/collections/{col['id']}/documents", headers=auth).json()
    assert [d["path"] for d in docs] == ["abs/x.md"]
    assert client.post("/api/knowledge/collections", headers=auth, json={"name": "handbook<script>"}).status_code == 422


def test_upload_requires_write_role_and_isolated(client, auth):
    col = make_collection(client, auth)
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"}
    )
    tok = client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()[
        "access_token"
    ]
    viewer = {"Authorization": f"Bearer {tok}"}
    assert upload(client, viewer, col["id"], {"a.md": "# A"}).status_code == 403
    other = register(client, "o@other.io", "Other")
    assert upload(client, other, col["id"], {"a.md": "# A"}).status_code == 404


def test_edit_and_delete_documents_update_index(client, auth):
    col = make_collection(client, auth)
    upload(client, auth, col["id"], {"a.md": "# A\n\nalpha text\n", "b.md": "# B\n\nbeta text\n"})
    assert upload(client, auth, col["id"], {"a.md": "# A\n\nalpha text\n"}).json()["changed"] == 0  # idempotent
    run = client.get(f"/api/repositories/{col['id']}/runs", headers=auth).json()
    assert run[0]["chunks_reused"] >= 2
    docs = client.get(f"/api/knowledge/collections/{col['id']}/documents", headers=auth).json()
    bid = next(d["id"] for d in docs if d["path"] == "b.md")
    assert client.delete(f"/api/knowledge/collections/{col['id']}/documents/{bid}", headers=auth).status_code == 204
    assert client.get(f"/api/repositories/{col['id']}", headers=auth).json()["file_count"] == 1
    hits = client.post("/api/knowledge/search", headers=auth, json={"query": "beta text"}).json()
    assert all("beta" not in h["content"] for h in hits)


def test_delete_collection_removes_graph(client, auth):
    col = make_collection(client, auth)
    upload(client, auth, col["id"], {"a.md": "# A\n\ntext about retries\n"})
    assert client.get("/api/graph/stats", headers=auth, params={"repo_id": col["id"]}).json()["total_nodes"] > 0
    assert client.delete(f"/api/repositories/{col['id']}", headers=auth).status_code == 204
    assert client.get("/api/graph/stats", headers=auth).json()["total_nodes"] == 0


# ---------------------------------------------------------------- retrieval over docs + code + graph
def test_search_returns_docs_code_and_connections(client, auth, indexed):
    col = make_collection(client, auth)
    upload(client, auth, col["id"], DOCS)
    hits = client.post(
        "/api/knowledge/search", headers=auth, json={"query": "what happens for a deleted user profile display name"}
    ).json()
    kinds = {h["kind"] for h in hits}
    assert kinds == {"doc", "code"}
    top_doc = next(h for h in hits if h["kind"] == "doc")
    assert any(
        c["label"] == "mentions" and c["node"]["qualname"] == "UserProfileService.get_profile"
        for c in top_doc["connections"]
    )
    only_docs = client.post(
        "/api/knowledge/search", headers=auth, json={"query": "deleted user profile", "kinds": ["doc"]}
    ).json()
    assert only_docs and all(h["kind"] == "doc" for h in only_docs if not h["via_graph"])


def test_graph_expansion_pulls_in_linked_code(client, auth, indexed):
    # the query only matches the doc wording; the code it mentions arrives through the graph
    hits = client.post(
        "/api/knowledge/search",
        headers=auth,
        json={"query": "three layers API handler profile service repository architecture", "k": 3},
    ).json()
    via = [h for h in hits if h["via_graph"]]
    assert via and via[0]["via_graph"]["relation"] in ("mentions", "related to", "links to", "calls", "called by")


def test_ask_extractive_without_llm_and_cites_sources(client, auth, indexed):
    r = client.post("/api/knowledge/ask", headers=auth, json={"query": "How are deleted users handled?"}).json()
    assert r["mode"] == "extractive" and "not generated" in r["answer"]
    assert r["sources"] and "[S1]" in r["answer"] or "[S2]" in r["answer"]
    assert r["connections"]


def test_ask_with_llm_cites_and_caches(client, auth, indexed, monkeypatch):
    from repolens.config import get_settings
    from repolens.services import llm
    from repolens.services.llm import LLMResult

    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    calls = []

    def fake(system, prompt, max_tokens, cfg=None):
        calls.append(prompt)
        assert "SOURCES" in prompt and "untrusted" in system
        return LLMResult("Deleted users get a default profile named Unknown [S1]. Also [S99].", 900, 40)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    q = {"query": "How are deleted users handled?"}
    a = client.post("/api/knowledge/ask", headers=auth, json=q).json()
    b = client.post("/api/knowledge/ask", headers=auth, json=q).json()
    assert a["mode"] == "llm" and a["unknown_citations"] == ["S99"] and b["cached"] is True and len(calls) == 1
    s = client.get("/api/usage/summary", headers=auth).json()
    assert any(w["name"] == "knowledge" for w in s["by_workflow"]) and s["totals"]["tokens_avoided"] == 940


def test_ask_nothing_found(client, auth, indexed):
    r = client.post("/api/knowledge/ask", headers=auth, json={"query": "zzqx kwyjibo flarbnitz"}).json()
    assert r["mode"] == "none" and r["sources"] == []


def test_kg_models_have_no_dangling_edges(client, auth, indexed, session):
    ids = {n.id for n in session.query(KGNode)}
    assert session.query(KGEdge).count() > 0
    assert all(e.src_id in ids and e.dst_id in ids for e in session.query(KGEdge))


def test_investigation_cites_related_docs_and_tests(client, auth, indexed):
    from tests.demo_repo import BUG_REPORT

    r = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [indexed["id"]]})
    inv = client.get(f"/api/investigations/{r.json()['id']}", headers=auth).json()
    assert inv["status"] == "completed" and inv["verification"]["valid"] is True
    kinds = {e["type"] for e in inv["hypotheses"][0]["evidence"]}
    assert {"code", "commit", "doc", "test"} <= kinds
    doc = next(e for e in inv["hypotheses"][0]["evidence"] if e["type"] == "doc")
    assert doc["file"] == "docs/architecture.md" and doc["relation"] == "MENTIONS"
    assert "Documented in" in inv["report_md"] and "Covered by test file" in inv["report_md"]


def test_extractive_answer_strips_markdown_links():
    from repolens.services.knowledge import _first_sentences

    assert _first_sentences("See [the guide](../a.md#x) for details. Next.") == "See the guide for details. Next."


def test_node_brief_reads_like_a_senior_engineers_notes(client, auth, indexed):
    nodes = client.get("/api/graph/nodes", headers=auth, params={"q": "get_profile", "type": "function"}).json()
    gp = next(n for n in nodes if n["qualname"] == "UserProfileService.get_profile")
    b = client.get(f"/api/graph/nodes/{gp['id']}/brief", headers=auth).json()
    assert b["summary"].startswith("Function UserProfileService.get_profile in src/user_profile.py")
    assert any(c["name"] == "handle_profile_request" for c in b["callers"]) and b["callers_total"] >= 1
    assert any(d["name"] == "find_by_id" or "find_by_id" in (d["qualname"] or "") for d in b["dependencies"])
    assert b["documented_in"] and b["cross_repo"] is False
    other = register(client, "y@other.io", "Other")
    assert client.get(f"/api/graph/nodes/{gp['id']}/brief", headers=other).status_code == 404
