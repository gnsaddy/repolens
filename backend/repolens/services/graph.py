"""Knowledge graph: files, symbols, doc sections and commits connected by typed, provenance-tagged edges.

Edges keep their origin (`provenance`) so static facts, doc references, git history and semantic guesses are never
conflated: static_analysis | doc_link | doc_mention | git | semantic | github.
"""

import hashlib
import posixpath
import re
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Change, CodeChunk, Document, KGEdge, KGNode, Repository
from .access import Scope
from .graph_insights import trust

SYMBOL_TYPES = {"function": "function", "method": "function", "class": "class", "interface": "class", "type": "class"}
CODE_LANGS = {"python", "javascript", "typescript", "tsx", "go", "java"}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$")
COMMON_CALLS = {
    "get",
    "set",
    "add",
    "put",
    "pop",
    "map",
    "len",
    "str",
    "int",
    "list",
    "dict",
    "print",
    "open",
    "init",
    "self",
    "append",
    "items",
    "keys",
    "values",
    "join",
    "split",
    "format",
    "strip",
    "run",
    "main",
    "log",
    "next",
    "then",
    "push",
    "filter",
    "reduce",
    "toString",
    "error",
    "info",
    "debug",
    "warn",
    "update",
    "delete",
    "close",
    "read",
    "write",
    "send",
    "start",
    "stop",
    "copy",
    "sort",
    "find",
    "index",
    "count",
    "min",
    "max",
    "sum",
    "range",
    "type",
}
MANAGED_BY_BUILDER = ("file", "doc", "section", "function", "class", "commit")


def node_id(org_id: str, repo_id: str, type_: str, key: str) -> str:
    return hashlib.sha1(f"{org_id}|{repo_id}|{type_}|{key}".encode()).hexdigest()[:32]


def edge_id(src: str, dst: str, rel: str) -> str:
    return hashlib.sha1(f"{src}|{dst}|{rel}".encode()).hexdigest()[:32]


@dataclass
class _Node:
    id: str
    type: str
    name: str
    qualname: str
    path: str
    start: int
    end: int
    chunk_id: str | None
    meta: dict


# ---------------------------------------------------------------------------------------------- import resolving
def resolve_import(stmt: str, lang: str, file_path: str, paths: set[str]) -> set[str]:
    out: set[str] = set()
    if lang == "python":
        m = re.match(r"\s*from\s+(\.*)([\w.]*)\s+import\s+(.*)", stmt)
        if m:
            dots, mod, names = m.group(1), m.group(2), m.group(3)
            base = posixpath.dirname(file_path)
            if dots:
                for _ in range(len(dots) - 1):
                    base = posixpath.dirname(base)
                roots = [base]
            else:
                roots = [""]
            modpath = mod.replace(".", "/")
            cands = []
            for r in roots:
                joined = posixpath.join(r, modpath) if modpath else r
                cands += [joined + ".py", joined + "/__init__.py"]
                for n in [x.strip().split(" as ")[0] for x in names.strip("()").split(",")]:
                    if n and n != "*":
                        cands += [posixpath.join(joined, n) + ".py"]
            out |= _match_paths(cands, paths, suffix=not dots)
        else:
            for mod in re.findall(r"import\s+([\w., ]+)", stmt):
                for part in mod.split(","):
                    p = part.strip().split(" as ")[0].replace(".", "/")
                    if p:
                        out |= _match_paths([p + ".py", p + "/__init__.py"], paths, suffix=True)
    elif lang in ("javascript", "typescript", "tsx"):
        for spec in re.findall(r"""from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]""", stmt):
            s = spec[0] or spec[1]
            if s.startswith("."):
                joined = posixpath.normpath(posixpath.join(posixpath.dirname(file_path), s))
                exts = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", "/index.ts", "/index.tsx", "/index.js"]
                out |= {joined + e for e in exts if joined + e in paths}
    elif lang == "java":
        m = re.match(r"\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;", stmt)
        if m:
            parts = m.group(1).split(".")
            for k in range(len(parts), 0, -1):
                out |= _match_paths(["/".join(parts[:k]) + ".java"], paths, suffix=True)
                if out:
                    break
    elif lang == "go":
        for s in re.findall(r'"([^"]+)"', stmt):
            tail = s.split("/")[-1]
            out |= {p for p in paths if p.endswith(".go") and posixpath.basename(posixpath.dirname(p)) == tail}
    out.discard(file_path)
    return out


def _match_paths(cands: list[str], paths: set[str], suffix: bool) -> set[str]:
    found = set()
    for c in cands:
        c = posixpath.normpath(c)
        if c in paths:
            found.add(c)
        elif suffix:
            found |= {p for p in paths if p.endswith("/" + c)}
    return found


# ---------------------------------------------------------------------------------------------- builder
def build_repo_graph(db: Session, repo: Repository) -> dict:
    org, rid = repo.org_id, repo.id
    chunks = list(
        db.scalars(
            select(CodeChunk).where(CodeChunk.repo_id == rid).order_by(CodeChunk.file_path, CodeChunk.start_line)
        )
    )
    nodes: dict[str, _Node] = {}
    edges: dict[str, KGEdge] = {}

    def add_node(
        type_: str,
        key: str,
        name: str,
        qual: str,
        path: str,
        start: int,
        end: int,
        chunk_id: str | None,
        meta: dict | None = None,
    ) -> _Node:
        nid = node_id(org, rid, type_, key)
        if nid not in nodes:
            nodes[nid] = _Node(nid, type_, name[:500], qual[:800], path, start, end, chunk_id, meta or {})
        return nodes[nid]

    def add_edge(src: str, dst: str, rel: str, prov: str, weight: float = 1.0, evidence: str = "") -> None:
        if src == dst:
            return
        eid = edge_id(src, dst, rel)
        cur = edges.get(eid)
        if cur is None:
            edges[eid] = KGEdge(
                id=eid,
                org_id=org,
                repo_id=rid,
                src_id=src,
                dst_id=dst,
                rel=rel,
                provenance=prov,
                weight=round(weight, 3),
                evidence=evidence[:400],
            )
        elif weight > cur.weight:
            cur.weight = round(weight, 3)

    files: dict[str, _Node] = {}
    lang_of: dict[str, str] = {}
    file_imports: dict[str, list[str]] = {}
    sym_by_key: dict[str, _Node] = {}
    sym_chunks: dict[str, CodeChunk] = {}
    section_by_path: dict[tuple[str, str], _Node] = {}
    section_chunks: list[tuple[_Node, CodeChunk]] = []
    by_name: dict[str, list[_Node]] = defaultdict(list)
    seen_keys: dict[str, int] = defaultdict(int)
    last_doc: dict[tuple[str, str], tuple[_Node, int]] = {}
    dup_sections: list[_Node] = []

    for c in chunks:
        lang_of[c.file_path] = c.language
        if c.file_path not in files:
            ftype = "doc" if c.language == "markdown" else "file"
            files[c.file_path] = add_node(
                ftype,
                c.file_path,
                posixpath.basename(c.file_path),
                c.file_path,
                c.file_path,
                1,
                c.end_line,
                c.id,
                {"language": c.language},
            )
        files[c.file_path].end = max(files[c.file_path].end, c.end_line)
        if c.language in CODE_LANGS and c.imports and c.file_path not in file_imports:
            file_imports[c.file_path] = list(c.imports)
        if c.chunk_type in SYMBOL_TYPES:
            qual = f"{c.parent_symbol}.{c.symbol_name}" if c.parent_symbol else c.symbol_name
            key = f"{c.file_path}#{qual}"
            if key in sym_by_key:
                continue  # split window of an already-registered symbol
            n = add_node(
                SYMBOL_TYPES[c.chunk_type],
                key,
                c.symbol_name,
                qual,
                c.file_path,
                c.start_line,
                c.end_line,
                c.id,
                {"kind": c.chunk_type},
            )
            sym_by_key[key] = n
            sym_chunks[n.id] = c
            by_name[c.symbol_name].append(n)
        elif c.chunk_type == "doc":
            full = f"{c.parent_symbol} > {c.symbol_name}" if c.parent_symbol else c.symbol_name
            prev = last_doc.get((c.file_path, full))
            if prev is not None and c.start_line <= prev[1]:  # overlapping window of the same section
                last_doc[(c.file_path, full)] = (prev[0], c.end_line)
                section_chunks.append((prev[0], c))
                continue
            base_key = f"{c.file_path}#{full}"
            seen_keys[base_key] += 1
            key = base_key if seen_keys[base_key] == 1 else f"{base_key}~{seen_keys[base_key]}"
            n = add_node(
                "section", key, c.symbol_name, full, c.file_path, c.start_line, c.end_line, c.id, {"heading_path": full}
            )
            if (c.file_path, full) not in section_by_path:
                section_by_path[(c.file_path, full)] = n
            else:
                dup_sections.append(n)  # same heading repeated in one file
            last_doc[(c.file_path, full)] = (n, c.end_line)
            section_chunks.append((n, c))

    paths = set(files)
    sym_nodes = [n for n in nodes.values() if n.type in ("function", "class")]
    qual_index: dict[str, list[_Node]] = defaultdict(list)
    for n in sym_nodes:
        qual_index[n.qualname].append(n)

    # org-wide names for cross-repository resolution (unique names only)
    foreign: dict[str, list[str]] = defaultdict(list)
    foreign_q: dict[str, list[str]] = defaultdict(list)  # "Class.method" is far less ambiguous than "method"
    for fid, fname, fqual in db.execute(
        select(KGNode.id, KGNode.name, KGNode.qualname).where(
            KGNode.org_id == org, KGNode.repo_id != rid, KGNode.type.in_(("function", "class"))
        )
    ):
        foreign[fname].append(fid)
        if fqual and fqual != fname:
            foreign_q[fqual].append(fid)

    # CONTAINS ------------------------------------------------------------------------------------------------
    for n in sym_nodes:
        parent_qual = n.qualname.rsplit(".", 1)[0] if "." in n.qualname else ""
        parent = sym_by_key.get(f"{n.path}#{parent_qual}") if parent_qual else None
        add_edge((parent or files[n.path]).id, n.id, "CONTAINS", "static_analysis")
    for (path, full), n in section_by_path.items():
        parent_path = full.rsplit(" > ", 1)[0] if " > " in full else ""
        parent = section_by_path.get((path, parent_path)) if parent_path else None
        add_edge((parent or files[path]).id, n.id, "CONTAINS", "doc_link")
    for n in dup_sections:
        add_edge(files[n.path].id, n.id, "CONTAINS", "doc_link")

    # IMPORTS --------------------------------------------------------------------------------------------------
    resolved_imports: dict[str, set[str]] = {}
    for path, stmts in file_imports.items():
        targets: set[str] = set()
        for stmt in stmts:
            targets |= resolve_import(stmt, lang_of[path], path, paths)
        resolved_imports[path] = targets
        for t in targets:
            add_edge(files[path].id, files[t].id, "IMPORTS", "static_analysis", 1.0, "import statement")

    # CALLS / TESTS ---------------------------------------------------------------------------------------------
    tested: dict[str, set[str]] = defaultdict(set)
    for n in sym_nodes:
        c = sym_chunks[n.id]
        imported = resolved_imports.get(n.path, set())
        for name in c.calls or []:
            if name in COMMON_CALLS or len(name) < 3:
                continue
            cands = by_name.get(name, [])
            cands = [x for x in cands if x.id != n.id]
            if cands:
                local = [x for x in cands if x.path == n.path]
                via_import = [x for x in cands if x.path in imported]
                pick, w = (local, 0.9) if local else (via_import, 1.0) if via_import else (cands, 0.6)
                if len(pick) > 3:
                    continue
                for tn in pick:
                    add_edge(
                        n.id,
                        tn.id,
                        "CALLS",
                        "static_analysis",
                        w / (1 if len(pick) == 1 else len(pick) ** 0.5),
                        f"{n.name} calls {name}()",
                    )
                    if TEST_PATH.search(n.path) and tn.path != n.path:
                        tested[n.path].add(tn.path)
            elif len(foreign.get(name, [])) == 1:
                add_edge(
                    n.id, foreign[name][0], "CALLS", "static_analysis", 0.4, f"{n.name} calls {name}() (cross-repo)"
                )
    for test_path, targets in tested.items():
        for t in targets:
            if not TEST_PATH.search(t):
                add_edge(
                    files[test_path].id, files[t].id, "TESTS", "static_analysis", 0.8, "test calls code under test"
                )

    # Doc LINKS_TO / MENTIONS ------------------------------------------------------------------------------------
    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    doc_rows: dict[str, Document] = {}
    if repo.source == "docs":
        doc_rows = {d.path: d for d in db.scalars(select(Document).where(Document.repo_id == rid))}
    heading_at: dict[str, tuple[int, str]] = {}  # file -> (line, name) of its first section
    for n, _c in section_chunks:
        cur = heading_at.get(n.path)
        if cur is None or n.start < cur[0]:
            heading_at[n.path] = (n.start, n.name)
    stem_index: dict[str, str] = {}
    title_index: dict[str, str] = {}
    for fpath, fnode in files.items():
        if fnode.type != "doc":
            continue
        row = doc_rows.get(fpath)
        title = (row.title if row and row.title and row.source_type == "confluence" else "") or heading_at.get(
            fpath, (0, "")
        )[1]
        fnode.meta["title"] = title or posixpath.basename(fpath)
        if row is not None and row.source_type != "upload":
            fnode.meta.update({"source_type": row.source_type, "source_url": row.source_url})
        stem_index.setdefault(norm(posixpath.splitext(posixpath.basename(fpath))[0]), fpath)
        if title:
            title_index.setdefault(norm(title), fpath)
    broken: dict[str, list[dict]] = defaultdict(list)
    for n, c in section_chunks:
        for link in c.imports or []:
            if link.startswith("wiki:"):
                name = norm(link[5:])
                path_hit = title_index.get(name) or stem_index.get(name)
                tgt = files[path_hit] if path_hit else None
                if tgt is None:
                    hit = [s for (_p, _full), s in section_by_path.items() if norm(s.name) == name]
                    tgt = hit[0] if hit else None
                if tgt is None and len(broken[n.path]) < 20:
                    broken[n.path].append({"section": n.name, "link": link})
            else:
                target, _, anchor = link.partition("#")
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(n.path), target)) if target else n.path
                tgt = files.get(resolved) if resolved in files else None
                if tgt is None and target and len(broken[n.path]) < 20:
                    broken[n.path].append({"section": n.name, "link": link})
                if tgt is not None and anchor:
                    from .docs import slugify

                    for (p, _full), s in section_by_path.items():
                        if p == resolved and slugify(s.name) == anchor.lower():
                            tgt = s
                            break
            if tgt is not None and tgt.id != n.id:
                add_edge(n.id, tgt.id, "LINKS_TO", "doc_link", 1.0, f"link: {link}"[:300])
        for tok in c.calls or []:
            tgts: list[tuple[str, float]] = []
            if "/" in tok or re.search(r"\.[a-z]{1,4}$", tok) and tok.lower() not in ("e.g", "i.e"):
                tgts = [(files[p].id, 0.9) for p in paths if p == tok or p.endswith("/" + tok)]
            if not tgts and tok in qual_index:
                cs = qual_index[tok]
                tgts = [(x.id, 0.9 if len(cs) == 1 else 0.5) for x in cs[:3]]
            if not tgts and tok in by_name and len(by_name[tok]) <= 3:
                k = len(by_name[tok])
                tgts = [(x.id, 0.7 if k == 1 else 0.4) for x in by_name[tok]]
            if not tgts and len(foreign_q.get(tok, [])) == 1:
                tgts = [(foreign_q[tok][0], 0.6)]  # unique qualified name in another repository
            if not tgts and len(foreign.get(tok, [])) == 1 and tok not in COMMON_CALLS:
                tgts = [(foreign[tok][0], 0.5)]
            for tid, w in tgts:
                add_edge(n.id, tid, "MENTIONS", "doc_mention", w, f"mentions `{tok}`")

    for fpath, items in broken.items():
        files[fpath].meta["broken_links"] = items
    # page hierarchy of imported wikis (Confluence ancestors): parent page -> child page
    for fpath, row in doc_rows.items():
        if row.parent_path and row.parent_path in files and fpath in files:
            add_edge(files[row.parent_path].id, files[fpath].id, "CONTAINS", "doc_link", 1.0, "page hierarchy")

    # MODIFIES (git) ----------------------------------------------------------------------------------------------
    for ch in db.scalars(select(Change).where(Change.repo_id == rid).order_by(Change.committed_at.desc()).limit(300)):
        touched = [f["path"] for f in ch.files if f["path"] in files]
        if not touched:
            continue
        msg = ch.message.splitlines()[0] if ch.message else ""
        cn = add_node(
            "commit",
            ch.sha,
            f"{ch.sha[:8]} {msg}"[:200],
            ch.sha,
            "",
            0,
            0,
            None,
            {"sha": ch.sha, "author": ch.author, "committed_at": ch.committed_at.isoformat()},
        )
        for p in touched:
            add_edge(cn.id, files[p].id, "MODIFIES", "git", 1.0, msg[:200])

    # RELATED_TO (semantic) ---------------------------------------------------------------------------------------
    _semantic_edges(add_edge, section_chunks, sym_nodes, sym_chunks, edges)

    return _persist(db, repo, nodes, edges)


def _semantic_edges(add_edge, section_chunks, sym_nodes, sym_chunks, edges) -> None:
    import numpy as np

    s = get_settings()
    docs = [(n, c) for n, c in section_chunks if c.embedding]
    cands = [(n, sym_chunks[n.id]) for n in sym_nodes if sym_chunks[n.id].embedding]
    cands += [(n, c) for n, c in docs]
    if not docs or len(cands) < 2 or len(docs) * len(cands) > 4_000_000:
        return
    a = np.array([c.embedding for _, c in docs], dtype=np.float32)
    b = np.array([c.embedding for _, c in cands], dtype=np.float32)
    sims = a @ b.T
    linked = {(e.src_id, e.dst_id) for e in edges.values() if e.rel in ("MENTIONS", "LINKS_TO")}
    for i, (dn, dc) in enumerate(docs):
        order = np.argsort(-sims[i])
        taken = 0
        for j in order:
            if sims[i, j] < s.kg_semantic_threshold or taken >= s.kg_semantic_top_k:
                break
            tn, tc = cands[int(j)]
            if tn.id == dn.id or tc.file_path == dc.file_path or (dn.id, tn.id) in linked:
                continue
            add_edge(dn.id, tn.id, "RELATED_TO", "semantic", float(sims[i, j]), "embedding similarity")
            taken += 1


def _persist(db: Session, repo: Repository, nodes: dict[str, _Node], edges: dict[str, KGEdge]) -> dict:
    org, rid = repo.org_id, repo.id
    existing = {
        n.id: n for n in db.scalars(select(KGNode).where(KGNode.repo_id == rid, KGNode.type.in_(MANAGED_BY_BUILDER)))
    }
    gone = [i for i in existing if i not in nodes]
    for i in range(0, len(gone), 500):
        batch = gone[i : i + 500]
        db.execute(delete(KGEdge).where(or_(KGEdge.src_id.in_(batch), KGEdge.dst_id.in_(batch))))
        db.execute(delete(KGNode).where(KGNode.id.in_(batch)))
    # derived edges are rebuilt; GitHub-sourced edges survive re-indexing
    db.execute(delete(KGEdge).where(KGEdge.repo_id == rid, KGEdge.provenance != "github"))
    db.flush()
    for nid, n in nodes.items():
        row = existing.get(nid)
        if row is None:
            db.add(
                KGNode(
                    id=nid,
                    org_id=org,
                    repo_id=rid,
                    type=n.type,
                    name=n.name,
                    qualname=n.qualname,
                    path=n.path,
                    start_line=n.start,
                    end_line=n.end,
                    chunk_id=n.chunk_id,
                    meta=n.meta,
                )
            )
        else:
            row.name, row.qualname, row.path, row.start_line, row.end_line = n.name, n.qualname, n.path, n.start, n.end
            row.chunk_id, row.meta = n.chunk_id, n.meta
    db.flush()
    valid_ids = set(nodes) | set(db.scalars(select(KGNode.id).where(KGNode.org_id == org, KGNode.repo_id != rid)))
    kept = [e for e in edges.values() if e.src_id in valid_ids and e.dst_id in valid_ids]
    db.add_all(kept)
    db.flush()
    refresh_degrees(db, org, rid)
    db.commit()
    by_rel: dict[str, int] = defaultdict(int)
    for e in kept:
        by_rel[e.rel] += 1
    return {"nodes": len(nodes), "edges": len(kept), "by_rel": dict(by_rel)}


def refresh_degrees(db: Session, org: str, repo_id: str) -> None:
    deg: dict[str, int] = defaultdict(int)
    for col in (KGEdge.src_id, KGEdge.dst_id):
        for nid, cnt in db.execute(select(col, func.count()).where(KGEdge.org_id == org).group_by(col)):
            deg[nid] += cnt
    for n in db.scalars(select(KGNode).where(KGNode.repo_id == repo_id)):
        n.degree = deg.get(n.id, 0)


# ---------------------------------------------------------------------------------------------- queries
def node_dict(n: KGNode, repo_names: dict[str, str] | None = None) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "name": n.name,
        "qualname": n.qualname,
        "path": n.path,
        "start_line": n.start_line,
        "end_line": n.end_line,
        "repo_id": n.repo_id,
        "repo": (repo_names or {}).get(n.repo_id, ""),
        "degree": n.degree,
        "meta": n.meta,
    }


def edge_dict(e: KGEdge) -> dict:
    return {
        "id": e.id,
        "source": e.src_id,
        "target": e.dst_id,
        "rel": e.rel,
        "provenance": e.provenance,
        "trust": trust(e.provenance, e.weight),
        "weight": e.weight,
        "evidence": e.evidence,
    }


def neighbors(
    db: Session,
    org: str,
    ids: list[str],
    rels: list[str] | None = None,
    limit: int = 60,
    min_weight: float = 0.0,
    scope: Scope | None = None,
) -> tuple[list[KGEdge], list[KGNode]]:
    q = select(KGEdge).where(
        KGEdge.org_id == org, or_(KGEdge.src_id.in_(ids), KGEdge.dst_id.in_(ids)), KGEdge.weight >= min_weight
    )
    if rels:
        q = q.where(KGEdge.rel.in_(rels))
    es = list(db.scalars(q.order_by(KGEdge.weight.desc()).limit(limit)))
    nids = {e.src_id for e in es} | {e.dst_id for e in es} | set(ids)
    ns = list(db.scalars(select(KGNode).where(KGNode.org_id == org, KGNode.id.in_(nids))))
    if scope is not None and scope.restricted:  # nodes (and every edge touching them) outside the scope vanish
        ns = [n for n in ns if scope.can(n.repo_id)]
        keep = {n.id for n in ns}
        es = [e for e in es if e.src_id in keep and e.dst_id in keep]
    return es, ns


def subgraph(
    db: Session,
    org: str,
    seeds: list[str],
    depth: int = 1,
    rels: list[str] | None = None,
    max_nodes: int = 200,
    scope: Scope | None = None,
) -> tuple[list[KGEdge], list[KGNode]]:
    frontier, seen = list(seeds), set(seeds)
    all_edges: dict[str, KGEdge] = {}
    for _ in range(depth):
        es, _ns = neighbors(db, org, frontier, rels, limit=max_nodes * 2, scope=scope)
        nxt = []
        for e in es:
            all_edges[e.id] = e
            for x in (e.src_id, e.dst_id):
                if x not in seen and len(seen) < max_nodes:
                    seen.add(x)
                    nxt.append(x)
        frontier = nxt
        if not frontier:
            break
    ns = list(db.scalars(select(KGNode).where(KGNode.org_id == org, KGNode.id.in_(seen))))
    if scope is not None and scope.restricted:
        ns = [n for n in ns if scope.can(n.repo_id)]
    ids = {n.id for n in ns}
    return [e for e in all_edges.values() if e.src_id in ids and e.dst_id in ids], ns


def find_path(
    db: Session, org: str, src: str, dst: str, max_depth: int = 6, scope: Scope | None = None
) -> list[tuple[str, KGEdge | None]]:
    """Shortest undirected path as [(node_id, edge_used_to_reach_it)]; [] when unreachable. Never routes through
    nodes outside `scope`."""
    prev: dict[str, tuple[str, KGEdge]] = {}
    repo_of: dict[str, str] = {}
    seen, q = {src}, deque([(src, 0)])
    while q:
        cur, d = q.popleft()
        if cur == dst:
            break
        if d >= max_depth:
            continue
        step = list(
            db.scalars(select(KGEdge).where(KGEdge.org_id == org, or_(KGEdge.src_id == cur, KGEdge.dst_id == cur)))
        )
        if scope is not None and scope.restricted:
            unknown = {x for e in step for x in (e.src_id, e.dst_id)} - repo_of.keys()
            if unknown:
                repo_of.update(
                    {i: r for i, r in db.execute(select(KGNode.id, KGNode.repo_id).where(KGNode.id.in_(unknown)))}
                )
        for e in step:
            nxt = e.dst_id if e.src_id == cur else e.src_id
            if scope is not None and scope.restricted and not scope.can(repo_of.get(nxt)):
                continue
            if nxt not in seen:
                seen.add(nxt)
                prev[nxt] = (cur, e)
                q.append((nxt, d + 1))
    if dst not in seen:
        return []
    path: list[tuple[str, KGEdge | None]] = []
    cur = dst
    while cur != src:
        p, e = prev[cur]
        path.append((cur, e))
        cur = p
    path.append((src, None))
    return list(reversed(path))


def brief(node: KGNode, conns: list[dict], repo_name: str = "") -> dict:
    """A senior engineer's read of one node: how it is used, what it depends on, where it is documented, risk."""

    def pick(rel: str, direction: str, types: tuple[str, ...] | None = None) -> list[dict]:
        return [
            c["node"]
            for c in conns
            if c["rel"] == rel and c["direction"] == direction and (types is None or c["node"]["type"] in types)
        ]

    callers = pick("CALLS", "in")
    calls = pick("CALLS", "out")
    docs = [c["node"] for c in conns if c["node"]["type"] in ("doc", "section")]
    history = [c["node"] for c in conns if c["node"]["type"] in ("pr", "commit")]
    repos = {x["repo"] for x in callers if x.get("repo")}
    cross = sorted(repos - {repo_name}) if repos else []
    short = lambda xs: [{k: x.get(k) for k in ("id", "name", "qualname", "type", "path", "repo")} for x in xs[:6]]  # noqa: E731
    public = not node.name.startswith("_")
    notes: list[str] = []
    if len(callers) >= 5:
        notes.append(f"Widely used: {len(callers)}+ callers, so a change here has a wide blast radius.")
    if cross:
        notes.append("Called from other repositories: coordinate changes with their owners.")
    if public and node.type in ("function", "method", "class") and not docs:
        notes.append("Public and not described in any document.")
    if node.type in ("function", "method", "class") and not callers:
        notes.append("Nothing calls it in the indexed code: it may be an entry point, or dead code.")
    if history:
        notes.append(f"Touched by {len(history)} recent pull request(s) or commit(s): read them before changing it.")
    kind = {"function": "Function", "method": "Method", "class": "Class", "file": "File", "doc": "Document"}.get(
        node.type, node.type.title()
    )
    where = f"{node.path}:{node.start_line}" if node.path and node.start_line else node.path
    return {
        "summary": f"{kind} {node.qualname or node.name} in {where}."
        + (f" Called by {len(callers)} place(s)" if callers else "")
        + (f" and depends on {len(calls)} other symbol(s)." if callers and calls else "." if callers else "")
        + (f" Described in {len(docs)} document section(s)." if docs else ""),
        "callers": short(callers),
        "dependencies": short(calls),
        "documented_in": short(docs),
        "history": short(history),
        "callers_total": len(callers),
        "cross_repo": bool(cross),
        "notes": notes,
    }
