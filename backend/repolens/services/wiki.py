"""DeepMind wiki: one browsable, searchable, analysed view over every documentation source the viewer may see
(READMEs in repositories, uploaded/pasted/URL docs, Confluence imports), connected to the code graph.

Everything is derived on read from the indexed chunks and the knowledge graph, always through the viewer's Scope,
so nothing here can reveal a restricted repository.
"""

import posixpath
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Change, CodeChunk, Document, KGEdge, KGNode, Repository
from ..security import redact_stored
from . import doc_intel, gitops
from .access import Scope
from .docs import slugify
from .graph import TEST_PATH, neighbors, node_dict

FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
WIKI_LINK = re.compile(r"(?<!\\)\[\[([^\]|#]+)(?:#([^\]|]*))?(?:\|([^\]]*))?\]\]")
MD_LINK = re.compile(r"(?<![!\\])\[([^\]]*)\]\(([^)\s]+)\)")
STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "you",
    "are",
    "not",
    "use",
    "see",
    "also",
    "note",
    "when",
    "then",
    "will",
    "can",
    "should",
    "must",
    "overview",
    "introduction",
    "summary",
    "usage",
    "example",
    "examples",
    "setup",
    "install",
    "installation",
    "todo",
    "notes",
    "details",
    "description",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _visible_repos(db: Session, org_id: str, scope: Scope) -> list[Repository]:
    return [
        r
        for r in db.scalars(select(Repository).where(Repository.org_id == org_id).order_by(Repository.name))
        if scope.can(r.id)
    ]


def _doc_nodes(db: Session, org_id: str, scope: Scope, repo_id: str | None = None) -> list[KGNode]:
    q = select(KGNode).where(KGNode.org_id == org_id, KGNode.type == "doc")
    ids = scope.ids_or_none()
    if ids is not None:
        q = q.where(KGNode.repo_id.in_(ids))
    if repo_id:
        q = q.where(KGNode.repo_id == repo_id)
    return list(db.scalars(q))


def _title(node: KGNode) -> str:
    return (node.meta or {}).get("title") or posixpath.splitext(posixpath.basename(node.path))[0].replace(
        "-", " "
    ).title()


def _source_label(node: KGNode, repo: Repository) -> str:
    return (node.meta or {}).get("source_type") or ("collection" if repo.source == "docs" else "repo")


# ------------------------------------------------------------------------------------------------ overview + tree
def overview(db: Session, org_id: str, scope: Scope) -> dict:
    repos = {r.id: r for r in _visible_repos(db, org_id, scope)}
    by_repo: dict[str, list[KGNode]] = defaultdict(list)
    for n in _doc_nodes(db, org_id, scope):
        by_repo[n.repo_id].append(n)
    sources: list[dict[str, Any]] = []
    for rid, nodes in by_repo.items():
        r = repos.get(rid)
        if r is None:
            continue
        kinds = Counter(_source_label(n, r) for n in nodes)
        doc_rows = (
            {d.path: d for d in db.scalars(select(Document).where(Document.repo_id == rid))}
            if r.source == "docs"
            else {}
        )
        synced = max((d.last_synced_at for d in doc_rows.values() if d.last_synced_at), default=None)
        sources.append(
            {
                "repo_id": rid,
                "name": r.name,
                "kind": "collection" if r.source == "docs" else "repo",
                "origin": kinds.most_common(1)[0][0],
                "pages": len(nodes),
                "visibility": r.visibility,
                "status": r.status,
                "last_synced_at": synced,
                "web_url": r.web_url,
            }
        )
    sources.sort(key=lambda s: (-s["pages"], s["name"]))
    sec_q = select(func.count()).select_from(KGNode).where(KGNode.org_id == org_id, KGNode.type == "section")
    visible_ids = scope.ids_or_none()
    if visible_ids is not None:
        sec_q = sec_q.where(KGNode.repo_id.in_(visible_ids))
    sections = db.scalar(sec_q) or 0
    return {
        "sources": sources,
        "totals": {"sources": len(sources), "pages": sum(s["pages"] for s in sources), "sections": sections},
    }


def tree(db: Session, org_id: str, scope: Scope, repo_id: str) -> list[dict]:
    """Nested page tree for one source: Confluence/page hierarchy when known, directories otherwise."""
    repo = db.get(Repository, repo_id)
    if repo is None or repo.org_id != org_id or not scope.can(repo.id):
        return []
    nodes = _doc_nodes(db, org_id, scope, repo_id)
    titles = {n.path: _title(n) for n in nodes}
    rows = (
        {d.path: d for d in db.scalars(select(Document).where(Document.repo_id == repo_id))}
        if repo.source == "docs"
        else {}
    )
    hierarchical = any(r.parent_path for r in rows.values())
    root: dict = {"children": {}}

    def add(parts: list[str], path: str) -> None:
        cur = root
        for i, part in enumerate(parts):
            cur = cur["children"].setdefault(part, {"name": part, "path": None, "children": {}})
            if i == len(parts) - 1:
                cur["path"] = path

    if hierarchical:
        chain: dict[str, list[str]] = {}

        def lineage(p: str, seen: set[str]) -> list[str]:
            row = rows.get(p)
            if row is None or not row.parent_path or row.parent_path in seen or row.parent_path not in rows:
                return [p]
            return lineage(row.parent_path, seen | {p}) + [p]

        for p in titles:
            chain[p] = lineage(p, set())
        for p, ch in sorted(chain.items(), key=lambda kv: (len(kv[1]), kv[0])):
            add(ch, p)
    else:
        for p in sorted(titles):
            add(p.split("/"), p)

    def render(d: dict) -> list[dict]:
        out = []
        for key, child in sorted(
            d["children"].items(), key=lambda kv: (bool(kv[1]["children"]) is False, kv[0].lower())
        ):
            path = child["path"]
            label = titles.get(path) if path else key.replace("-", " ").replace("_", " ")
            out.append({"key": key, "title": label, "path": path, "children": render(child)})
        return out

    return render(root)


# ------------------------------------------------------------------------------------------------ pages
def _page_text(db: Session, repo: Repository, path: str) -> str | None:
    if repo.source == "docs":
        row = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == path))
        return row.content if row else None
    checkout = repo.local_path or (str(Path(repo.url).expanduser()) if repo.url else "")
    if not checkout or not repo.indexed_sha:
        return None
    try:
        return gitops.read_file(checkout, repo.indexed_sha, path).decode("utf-8", errors="replace")
    except gitops.GitError:
        return None


def resolve_links(db: Session, org_id: str, scope: Scope, repo_id: str, path: str, text: str) -> tuple[str, list[dict]]:
    """Rewrite wiki/relative links to wiki://repo/path targets; return the text and the list of unresolved links."""
    nodes = _doc_nodes(db, org_id, scope)
    own = [n for n in nodes if n.repo_id == repo_id]
    by_title: dict[str, KGNode] = {}
    for n in [*own, *[x for x in nodes if x.repo_id != repo_id]]:  # same-repo pages win
        by_title.setdefault(_norm(_title(n)), n)
        by_title.setdefault(_norm(posixpath.splitext(posixpath.basename(n.path))[0]), n)
    paths = {n.path for n in own}
    broken: list[dict] = []

    def wiki(m: re.Match) -> str:
        title, anchor, label = m.group(1).strip(), (m.group(2) or "").strip(), (m.group(3) or "").strip()
        n = by_title.get(_norm(title))
        shown = label or title
        if n is None:
            broken.append({"link": title})
            return f"~~{shown}~~"
        frag = f"#{slugify(anchor)}" if anchor else ""
        return f"[{shown}](wiki://{n.repo_id}/{n.path}{frag})"

    def rel(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if re.match(r"^(https?:|mailto:|tel:|wiki:|#)", url):  # javascript:, data:, vbscript: ... never pass through
            return m.group(0)
        target, _, frag = url.partition("#")
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target)) if target else path
        if resolved in paths:
            return f"[{label}](wiki://{repo_id}/{resolved}{('#' + frag) if frag else ''})"
        if target:
            broken.append({"link": url})
        return label

    text = FRONT_MATTER.sub("", text, count=1)
    text = WIKI_LINK.sub(wiki, text)
    text = MD_LINK.sub(rel, text)
    return text, broken


def page(db: Session, org_id: str, scope: Scope, repo_id: str, path: str) -> dict | None:
    repo = db.get(Repository, repo_id)
    if repo is None or repo.org_id != org_id or not scope.can(repo.id):
        return None
    node = db.scalar(
        select(KGNode).where(
            KGNode.org_id == org_id, KGNode.repo_id == repo_id, KGNode.path == path, KGNode.type == "doc"
        )
    )
    raw = _page_text(db, repo, path)
    if node is None or raw is None:
        return None
    markdown, broken = resolve_links(db, org_id, scope, repo_id, path, redact_stored(raw)[0])
    names = {r.id: r.name for r in _visible_repos(db, org_id, scope)}
    sections = list(
        db.scalars(
            select(KGNode)
            .where(KGNode.repo_id == repo_id, KGNode.path == path, KGNode.type == "section")
            .order_by(KGNode.start_line)
        )
    )
    sec_ids = [s.id for s in sections]
    back: dict[tuple[str, str], dict] = {}
    out_links: dict[tuple[str, str], dict] = {}
    code: dict[str, dict] = {}
    related: dict[tuple[str, str], dict] = {}
    page_titles = {(n.repo_id, n.path): _title(n) for n in _doc_nodes(db, org_id, scope)}
    es, ns = neighbors(db, org_id, [node.id, *sec_ids], limit=400, scope=scope)
    nmap = {n.id: n for n in ns}
    own = {node.id, *sec_ids}
    for e in es:
        a, b = nmap.get(e.src_id), nmap.get(e.dst_id)
        if a is None or b is None:
            continue
        inbound = e.dst_id in own and e.src_id not in own
        other, mine = (a, b) if inbound else (b, a)
        if e.src_id in own and e.dst_id in own:
            continue
        key = (other.repo_id, other.path)
        if e.rel == "LINKS_TO" and other.type in ("section", "doc"):
            target = back if inbound else out_links
            cur = target.setdefault(
                key,
                {
                    "repo": names.get(other.repo_id, ""),
                    "repo_id": other.repo_id,
                    "path": other.path,
                    "title": page_titles.get(key) or other.meta.get("title") or other.name,
                    "sections": [],
                },
            )
            cur["sections"].append(other.name if inbound else mine.name)
        elif e.rel == "MENTIONS" and not inbound and other.type in ("function", "class", "file"):
            code[other.id] = {**node_dict(other, names), "evidence": e.evidence, "weight": e.weight}
        elif e.rel == "RELATED_TO" and other.type in ("section", "doc") and other.path != path:
            cur = related.setdefault(
                key,
                {
                    "repo": names.get(other.repo_id, ""),
                    "repo_id": other.repo_id,
                    "path": other.path,
                    "title": page_titles.get(key) or other.name,
                    "weight": 0.0,
                    "via": e.provenance,
                },
            )
            cur["weight"] = max(cur["weight"], e.weight)
    crumbs = _breadcrumbs(db, repo, path, node)
    doc_row = (
        db.scalar(select(Document).where(Document.repo_id == repo_id, Document.path == path))
        if repo.source == "docs"
        else None
    )
    return {
        "repo_id": repo_id,
        "repo": repo.name,
        "path": path,
        "title": _title(node),
        "markdown": markdown.strip(),
        "unresolved_links": broken,
        "breadcrumbs": crumbs,
        "outline": [
            {"title": s.name, "path": s.qualname, "line": s.start_line, "anchor": slugify(s.name)} for s in sections
        ],
        "source": {
            "type": _source_label(node, repo),
            "url": (doc_row.source_url if doc_row else "") or "",
            "version": doc_row.source_version if doc_row else None,
            "synced_at": doc_row.last_synced_at if doc_row else None,
            "updated_at": (doc_row.source_updated_at or doc_row.updated_at) if doc_row else None,
            "repo_url": repo.web_url,
        },
        "deletable": bool(
            repo.source == "docs" and doc_row is not None and doc_row.source_type in ("upload", "url", "")
        ),
        "managed_by": doc_row.source_type
        if doc_row is not None and doc_row.source_type not in ("upload", "url", "")
        else None,
        "backlinks": sorted(back.values(), key=lambda x: x["title"]),
        "links_out": sorted(out_links.values(), key=lambda x: x["title"]),
        "mentions_code": sorted(code.values(), key=lambda x: -x["weight"])[:25],
        "related": sorted(related.values(), key=lambda x: -x["weight"])[:8],
        "concepts": [c["term"] for c in concept_index(db, org_id, scope, min_docs=2, only=(repo_id, path))[:12]],
        "insights": doc_intel.insights(path, _title(node), raw),
    }


def _breadcrumbs(db: Session, repo: Repository, path: str, node: KGNode) -> list[dict]:
    crumbs = [{"title": repo.name, "path": None}]
    if repo.source == "docs":
        rows = {d.path: d for d in db.scalars(select(Document).where(Document.repo_id == repo.id))}
        chain, cur, seen = [], rows.get(path), set()
        while cur is not None and cur.parent_path and cur.parent_path in rows and cur.parent_path not in seen:
            seen.add(cur.parent_path)
            cur = rows[cur.parent_path]
            chain.append({"title": cur.title or posixpath.basename(cur.path), "path": cur.path})
        if chain:
            return crumbs + list(reversed(chain))
    parts = path.split("/")[:-1]
    return crumbs + [{"title": p, "path": None} for p in parts]


# ------------------------------------------------------------------------------------------------ concepts
FILE_EXT = re.compile(
    r"\.(py|jsx?|tsx?|go|java|mdx?|rst|txt|json|ya?ml|toml|ini|cfg|conf|xml|html?|css|sh|sql|rb|rs|c|h|cpp|cs|kt|lock|env|properties)$"
)
_CODE_LIKE = re.compile(r"[A-Za-z0-9]+_[A-Za-z0-9_]+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|\w+\.\w+")


def _doc_chunks(db: Session, org_id: str, scope: Scope, only: tuple[str, str] | None = None) -> list[CodeChunk]:
    q = select(CodeChunk).where(CodeChunk.org_id == org_id, CodeChunk.chunk_type == "doc")
    ids = scope.ids_or_none()
    if ids is not None:
        q = q.where(CodeChunk.repo_id.in_(ids))
    if only:
        q = q.where(CodeChunk.repo_id == only[0], CodeChunk.file_path == only[1])
    return list(db.scalars(q.limit(60_000)))


def _terms_of(c: CodeChunk) -> set[str]:
    terms = {t.strip() for t in (c.calls or []) if len(t.strip()) >= 3}
    heading = (c.symbol_name or "").strip()
    if 3 <= len(heading) <= 60:
        terms.add(heading)
    return {t for t in terms if t.lower() not in STOP_TERMS}


def concept_index(
    db: Session, org_id: str, scope: Scope, min_docs: int = 2, only: tuple[str, str] | None = None
) -> list[dict]:
    """Terms that recur across documents: identifiers/paths mentioned in backticks or prose, and repeated headings.
    Computed from the viewer's visible chunks only (so a concept never reveals a hidden document)."""
    all_chunks = _doc_chunks(db, org_id, scope)
    docs_of: dict[str, set[tuple[str, str]]] = defaultdict(set)
    sections_of: Counter[str] = Counter()
    display: dict[str, str] = {}
    for c in all_chunks:
        for t in _terms_of(c):
            key = t.lower()
            docs_of[key].add((c.repo_id, c.file_path))
            sections_of[key] += 1
            display.setdefault(key, t)
    wanted: set[str] | None = None
    if only:
        wanted = {k for c in all_chunks if (c.repo_id, c.file_path) == only for k in (t.lower() for t in _terms_of(c))}
    code_q = select(KGNode.name).where(KGNode.org_id == org_id, KGNode.type.in_(("function", "class")))
    visible_ids = scope.ids_or_none()
    if visible_ids is not None:
        code_q = code_q.where(KGNode.repo_id.in_(visible_ids))
    names_l = {n.lower() for (n,) in db.execute(code_q)}
    out: list[dict[str, Any]] = []
    for key, docs in docs_of.items():
        if wanted is not None and key not in wanted:
            continue
        code_like = bool(_CODE_LIKE.fullmatch(display[key])) or key.endswith("()")
        if len(docs) < min_docs and not (
            len(docs) >= 1 and code_like and key.split(".")[-1] in names_l and min_docs <= 1
        ):
            continue
        out.append(
            {
                "term": display[key],
                "docs": len(docs),
                "sections": sections_of[key],
                "code_like": code_like,
                "in_code": key.split(".")[-1] in names_l,
            }
        )
    out.sort(key=lambda c: (-c["docs"], not c["in_code"], -c["sections"], c["term"].lower()))
    return out


def concept_detail(db: Session, org_id: str, scope: Scope, term: str) -> dict | None:
    key = term.strip().lower()
    if not key:
        return None
    names = {r.id: r.name for r in _visible_repos(db, org_id, scope)}
    mentions: list[dict] = []
    definitions: list[dict] = []
    co: Counter[str] = Counter()
    for c in _doc_chunks(db, org_id, scope):
        terms = _terms_of(c)
        low = {t.lower() for t in terms}
        in_text = key in c.content.lower() and len(key) >= 4
        if key not in low and not in_text:
            continue
        item = {
            "repo_id": c.repo_id,
            "repo": names.get(c.repo_id, ""),
            "path": c.file_path,
            "section": c.symbol_name,
            "snippet": c.content.strip()[:300],
        }
        (definitions if (c.symbol_name or "").strip().lower() == key else mentions).append(item)
        for t in terms:
            if t.lower() != key:
                co[t] += 1
    if not definitions and not mentions:
        return None
    q = select(KGNode).where(KGNode.org_id == org_id, KGNode.type.in_(("function", "class")))
    ids = scope.ids_or_none()
    if ids is not None:
        q = q.where(KGNode.repo_id.in_(ids))
    code = [
        {**node_dict(n, names)}
        for n in db.scalars(q)
        if n.name.lower() == key.split(".")[-1] or n.qualname.lower() == key
    ][:10]
    lead = (definitions or mentions)[0]
    body = [ln for ln in lead["snippet"].splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return {
        "term": term,
        "summary": " ".join(body)[:400],
        "summary_source": {"repo": lead["repo"], "path": lead["path"], "section": lead["section"]},
        "definitions": definitions[:10],
        "mentions": mentions[:30],
        "code": code,
        "related_terms": [{"term": t, "count": n} for t, n in co.most_common(10)],
        "documents": len({(m["repo_id"], m["path"]) for m in [*definitions, *mentions]}),
    }


# ------------------------------------------------------------------------------------------------ health
def health(db: Session, org_id: str, scope: Scope, stale_days: int = 180, limit: int = 40) -> dict:
    repos = {r.id: r for r in _visible_repos(db, org_id, scope)}
    doc_nodes = [n for n in _doc_nodes(db, org_id, scope) if n.repo_id in repos]
    ids = [n.id for n in doc_nodes]
    sec_to_file: dict[str, str] = {}
    sections = list(
        db.scalars(
            select(KGNode).where(
                KGNode.org_id == org_id, KGNode.type == "section", KGNode.repo_id.in_(list(repos) or ["-"])
            )
        )
    )
    file_node = {(n.repo_id, n.path): n for n in doc_nodes}
    for s in sections:
        fn = file_node.get((s.repo_id, s.path))
        if fn is not None:
            sec_to_file[s.id] = fn.id
    cut = datetime.now(UTC) - timedelta(days=stale_days)
    names = lambda rid: repos[rid].name  # noqa: E731

    # --- orphans: nothing links to the page and no parent page contains it
    inbound_linked: set[str] = set()
    tree_roots: set[str] = set()  # pages that contain other pages are entry points, not orphans
    for e in db.scalars(
        select(KGEdge).where(
            KGEdge.org_id == org_id, KGEdge.rel.in_(("LINKS_TO", "CONTAINS")), KGEdge.repo_id.in_(list(repos) or ["-"])
        )
    ):
        src_file = sec_to_file.get(e.src_id, e.src_id if e.src_id in ids else None)
        dst_file = sec_to_file.get(e.dst_id, e.dst_id if e.dst_id in ids else None)
        if dst_file and src_file and dst_file != src_file:
            inbound_linked.add(dst_file)
            if e.rel == "CONTAINS":
                tree_roots.add(src_file)
    per_repo = Counter(n.repo_id for n in doc_nodes)
    orphans = [
        {"repo": names(n.repo_id), "repo_id": n.repo_id, "path": n.path, "title": _title(n)}
        for n in doc_nodes
        if n.id not in inbound_linked
        and n.id not in tree_roots
        and per_repo[n.repo_id] > 1
        and not re.match(r"^readme", posixpath.basename(n.path), re.I)
    ]

    broken = [
        {"repo": names(n.repo_id), "repo_id": n.repo_id, "path": n.path, "title": _title(n), **b}
        for n in doc_nodes
        for b in (n.meta or {}).get("broken_links", [])
    ]

    # --- stale pages
    stale: list[dict] = []
    docs_rows = {
        (d.repo_id, d.path): d for d in db.scalars(select(Document).where(Document.repo_id.in_(list(repos) or ["-"])))
    }
    commit_dates: dict[tuple[str, str], datetime] = {}
    git_repos = [rid for rid, r in repos.items() if r.source != "docs"]
    for ch in db.scalars(
        select(Change).where(Change.repo_id.in_(git_repos or ["-"])).order_by(Change.committed_at.desc()).limit(5000)
    ):
        committed = ch.committed_at if ch.committed_at.tzinfo else ch.committed_at.replace(tzinfo=UTC)
        for f in ch.files:
            commit_dates.setdefault((ch.repo_id, f["path"]), committed)
    for n in doc_nodes:
        row = docs_rows.get((n.repo_id, n.path))
        when: datetime | None = (
            (row.source_updated_at or row.updated_at) if row else commit_dates.get((n.repo_id, n.path))
        )
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when is not None and when < cut:
            stale.append(
                {
                    "repo": names(n.repo_id),
                    "repo_id": n.repo_id,
                    "path": n.path,
                    "title": _title(n),
                    "last_updated": when,
                    "age_days": (datetime.now(UTC) - when).days,
                }
            )
    stale.sort(key=lambda x: -x["age_days"])

    # --- near-duplicate content across different pages
    dup: dict[tuple[str, str], dict] = {}
    for e in db.scalars(
        select(KGEdge).where(
            KGEdge.org_id == org_id,
            KGEdge.rel == "RELATED_TO",
            KGEdge.weight >= 0.85,
            KGEdge.repo_id.in_(list(repos) or ["-"]),
        )
    ):
        a, b = sec_to_file.get(e.src_id), sec_to_file.get(e.dst_id)
        if a and b and a != b:
            key = (min(a, b), max(a, b))
            cur = dup.setdefault(key, {"a": a, "b": b, "similarity": 0.0})
            cur["similarity"] = max(cur["similarity"], e.weight)
    by_id = {n.id: n for n in doc_nodes}
    duplicates = [
        {
            "similarity": round(d["similarity"], 2),
            "a": {"repo": names(by_id[d["a"]].repo_id), "path": by_id[d["a"]].path, "title": _title(by_id[d["a"]])},
            "b": {"repo": names(by_id[d["b"]].repo_id), "path": by_id[d["b"]].path, "title": _title(by_id[d["b"]])},
        }
        for d in sorted(dup.values(), key=lambda x: -x["similarity"])
        if d["a"] in by_id and d["b"] in by_id
    ]

    # --- important code nobody documents
    documented = {
        e.dst_id
        for e in db.scalars(
            select(KGEdge).where(
                KGEdge.org_id == org_id, KGEdge.rel == "MENTIONS", KGEdge.src_id.in_(list(sec_to_file) or ["-"])
            )
        )
    }
    code_nodes = [
        n
        for n in db.scalars(
            select(KGNode).where(
                KGNode.org_id == org_id,
                KGNode.type.in_(("function", "class")),
                KGNode.repo_id.in_(list(repos) or ["-"]),
            )
        )
        if not n.name.startswith("_") and not TEST_PATH.search(n.path)
    ]
    undocumented = sorted((n for n in code_nodes if n.id not in documented), key=lambda n: -n.degree)
    ratio = 1 - len(undocumented) / len(code_nodes) if code_nodes else None

    # --- doc references to code that no longer exists
    known = {n.name.lower() for n in code_nodes} | {n.qualname.lower() for n in code_nodes}
    known |= {
        n.name.lower()
        for n in db.scalars(
            select(KGNode).where(
                KGNode.org_id == org_id, KGNode.type == "file", KGNode.repo_id.in_(list(repos) or ["-"])
            )
        )
    }
    known |= {
        n.path.lower()
        for n in db.scalars(
            select(KGNode).where(
                KGNode.org_id == org_id, KGNode.type == "file", KGNode.repo_id.in_(list(repos) or ["-"])
            )
        )
    }
    qualified = {n.qualname.lower() for n in code_nodes}
    stale_refs: list[dict] = []
    seen_ref: set[tuple[str, str, str]] = set()
    has_code = bool(code_nodes)
    if has_code:
        for c in _doc_chunks(db, org_id, scope):
            for t in c.calls or []:
                low = t.lower().removesuffix("()")
                if not _CODE_LIKE.fullmatch(t.removesuffix("()")) or "/" in t or FILE_EXT.search(low):
                    continue
                if "." in low:
                    # a qualified reference must match a full qualified name; judge only class-like prefixes
                    # (otherwise `os.path` or `settings.DEBUG` would be flagged)
                    if not t[:1].isupper() or low in qualified:
                        continue
                elif low in known:
                    continue
                k = (c.repo_id, c.file_path, low)
                if k not in seen_ref:
                    seen_ref.add(k)
                    stale_refs.append(
                        {
                            "repo": names(c.repo_id),
                            "repo_id": c.repo_id,
                            "path": c.file_path,
                            "section": c.symbol_name,
                            "term": t,
                        }
                    )

    def cap(xs: list) -> list:
        return xs[:limit]

    return {
        "summary": {
            "pages": len(doc_nodes),
            "orphans": len(orphans),
            "broken_links": len(broken),
            "stale": len(stale),
            "duplicates": len(duplicates),
            "undocumented_code": len(undocumented),
            "outdated_references": len(stale_refs),
            "documented_code_ratio": round(ratio, 3) if ratio is not None else None,
            "stale_days": stale_days,
        },
        "orphans": cap(orphans),
        "broken_links": cap(broken),
        "stale": cap(stale),
        "duplicates": cap(duplicates),
        "undocumented_code": cap([{**node_dict(n, {r.id: r.name for r in repos.values()})} for n in undocumented]),
        "outdated_references": cap(stale_refs),
    }


# ------------------------------------------------------------------------------------------------ search and atlas
def search_pages(db: Session, org_id: str, scope: Scope, query: str, limit: int = 15) -> list[dict]:
    """Documentation pages that answer a query, best first, one row per page."""
    from . import knowledge as kb

    best: dict[tuple[str, str], dict] = {}
    for r in kb.search(db, org_id, query, scope.ids_or_none(), k=limit * 3, scope=scope, kinds=["doc"]):
        key = (r["repo_id"], r["file"])
        if key in best:
            continue
        best[key] = {
            "repo": r["repo"],
            "repo_id": r["repo_id"],
            "path": r["file"],
            "title": r["title"] or posixpath.basename(r["file"]),
            "snippet": re.sub(r"\s+", " ", r["content"])[:240],
            "start_line": r["start_line"],
            "score": round(float(r["score"]), 3),
        }
    return list(best.values())[:limit]


def _module_of(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    if parts[0] in ("src", "lib", "app", "packages", "pkg", "internal", "cmd") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def atlas(db: Session, org_id: str, scope: Scope, repo_id: str) -> dict:
    """A map of a code repository built from its graph: modules, their key symbols, and what is undocumented."""
    nodes = list(
        db.scalars(
            select(KGNode).where(
                KGNode.org_id == org_id,
                KGNode.repo_id == repo_id,
                KGNode.type.in_(("file", "function", "class", "method")),
            )
        )
    )
    section_ids = set(
        db.scalars(select(KGNode.id).where(KGNode.org_id == org_id, KGNode.type == "section"))
    )  # doc sections anywhere in the workspace may document this code
    documented = {
        e.dst_id
        for e in db.scalars(select(KGEdge).where(KGEdge.org_id == org_id, KGEdge.rel == "MENTIONS"))
        if e.src_id in section_ids
    }
    mods: dict[str, dict] = {}
    for n in nodes:
        if TEST_PATH.search(n.path):
            continue
        m = mods.setdefault(
            _module_of(n.path),
            {"files": 0, "functions": 0, "classes": 0, "documented": 0, "symbols": [], "langs": Counter()},
        )
        if n.type == "file":
            m["files"] += 1
            m["langs"][posixpath.splitext(n.path)[1].lstrip(".") or "other"] += 1
        elif not n.name.startswith("_"):
            m["classes" if n.type == "class" else "functions"] += 1
            doc = n.id in documented
            m["documented"] += doc
            m["symbols"].append((n, doc))
    out = []
    for name, m in mods.items():
        public = m["functions"] + m["classes"]
        top = sorted(m["symbols"], key=lambda t: -t[0].degree)[:5]
        out.append(
            {
                "module": name,
                "files": m["files"],
                "functions": m["functions"],
                "classes": m["classes"],
                "languages": [k for k, _ in m["langs"].most_common(3)],
                "documented": m["documented"],
                "coverage": round(m["documented"] / public, 2) if public else None,
                "top_symbols": [
                    {
                        "node_id": n.id,
                        "name": n.qualname or n.name,
                        "type": n.type,
                        "path": n.path,
                        "start_line": n.start_line,
                        "connections": n.degree,
                        "documented": doc,
                    }
                    for n, doc in top
                ],
            }
        )
    out.sort(key=lambda x: -(x["functions"] + x["classes"]))
    total = sum(x["functions"] + x["classes"] for x in out)
    docd = sum(x["documented"] for x in out)
    return {
        "modules": out,
        "summary": {
            "modules": len(out),
            "files": sum(x["files"] for x in out),
            "public_symbols": total,
            "documented": docd,
            "coverage": round(docd / total, 2) if total else None,
        },
    }
