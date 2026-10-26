"""What the graph says about a repository as a whole: how far each edge can be trusted, which parts hang together
(communities), where change is risky (hubs), which links cut across parts, and a ready-made "start here" page.

Nothing here calls a model. Communities come from label propagation over the stored edges, in a fixed order, so the same graph
always gives the same result."""

import posixpath
from collections import Counter, defaultdict
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KGEdge, KGNode, Repository

ELIGIBLE = ("file", "doc", "section", "function", "class", "method")
HUB_TYPES = ("file", "doc", "function", "class", "method")
FACT = {"static_analysis", "doc_link", "git", "github"}
MIN_COMMUNITY = 3
_PLAIN = {"CONTAINS", "MODIFIES"}  # structure and history, never a surprising link
_cache: dict[tuple, dict] = {}


def trust(provenance: str, weight: float = 1.0) -> str:
    """fact: found directly (code analysis, document links, git, GitHub). inferred: guessed from words or meaning, with a
    similarity that is high enough to show. uncertain: the same, but weak."""
    if provenance in FACT:
        return "fact"
    return "inferred" if weight >= 0.5 else "uncertain"


def label_propagation(ids: Iterable[str], edges: Iterable[tuple[str, str, float]], rounds: int = 15) -> dict[str, int]:
    """Deterministic community detection: each node takes the label most of its weighted neighbours have (ties go to the
    smaller label), visited in sorted order, until nothing changes. Returns node id -> community number, 0 being the largest.
    Groups smaller than MIN_COMMUNITY are left out."""
    order = sorted(set(ids))
    index = {n: i for i, n in enumerate(order)}
    adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for a, b, w in edges:
        if a in index and b in index and a != b:
            adj[index[a]].append((index[b], w))
            adj[index[b]].append((index[a], w))
    label = list(range(len(order)))
    for _ in range(rounds):
        changed = False
        for i in range(len(order)):
            if not adj[i]:
                continue
            score: dict[int, float] = defaultdict(float)
            for j, w in adj[i]:
                score[label[j]] += w
            best = min(score, key=lambda k: (-score[k], k))
            if best != label[i] and score[best] > score.get(label[i], 0.0):
                label[i], changed = best, True
        if not changed:
            break
    groups: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(label):
        groups[lab].append(i)
    kept = sorted((m for m in groups.values() if len(m) >= MIN_COMMUNITY), key=lambda m: (-len(m), m[0]))
    return {order[i]: c for c, members in enumerate(kept) for i in members}


def _area(path: str) -> str:
    parts = [x for x in posixpath.dirname(path).split("/") if x]
    return "/".join(parts[:3]) or "(top level)"


def analyse(db: Session, repo: Repository) -> dict:
    """Communities, hubs and surprising links of one repository (cached until it is indexed again)."""
    key = (repo.id, repo.indexed_sha, repo.file_count, repo.chunk_count)
    if key in _cache:
        return _cache[key]
    nodes = {n.id: n for n in db.scalars(select(KGNode).where(KGNode.repo_id == repo.id, KGNode.type.in_(ELIGIBLE)))}
    edges = [
        e
        for e in db.scalars(select(KGEdge).where(KGEdge.repo_id == repo.id))
        if e.src_id in nodes and e.dst_id in nodes
    ]
    member = label_propagation(nodes, ((e.src_id, e.dst_id, max(e.weight, 0.05)) for e in edges))
    by_comm: dict[int, list[KGNode]] = defaultdict(list)
    for nid, c in member.items():
        by_comm[c].append(nodes[nid])
    communities = []
    for c in sorted(by_comm):
        ms = by_comm[c]
        files = [n for n in ms if n.type in ("file", "doc")]
        area = Counter(_area(n.path) for n in files or ms).most_common(1)
        top = sorted((n for n in ms if n.type in HUB_TYPES), key=lambda n: (-n.degree, n.id))[:3]
        communities.append(
            {
                "id": c,
                "size": len(ms),
                "files": len(files),
                "label": area[0][0] if area else (top[0].name if top else f"cluster {c + 1}"),
                "top": [{"id": n.id, "name": n.qualname or n.name, "type": n.type, "degree": n.degree} for n in top],
            }
        )
    hubs = sorted((n for n in nodes.values() if n.type in HUB_TYPES), key=lambda n: (-n.degree, n.id))[:10]
    pair_count: Counter = Counter()
    cross = []
    for e in edges:
        a, b = member.get(e.src_id), member.get(e.dst_id)
        if a is None or b is None or a == b or e.rel in _PLAIN:
            continue
        pair_count[tuple(sorted((a, b)))] += 1
        cross.append((e, a, b))
    ranked = sorted(
        cross,
        key=lambda t: (pair_count[tuple(sorted((t[1], t[2])))], -t[0].weight, t[0].id),
    )
    surprising, seen_pairs = [], set()
    for e, a, b in ranked:
        pair = tuple(sorted((a, b)))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        surprising.append({"edge": e, "src": nodes[e.src_id], "dst": nodes[e.dst_id], "a": a, "b": b})
        if len(surprising) >= 8:
            break
    result = {
        "member": member,
        "communities": communities,
        "hubs": hubs,
        "surprising": surprising,
        "nodes": len(nodes),
        "edges": len(edges),
    }
    if len(_cache) >= 32:
        _cache.pop(next(iter(_cache)))
    _cache[key] = result
    return result


def _ref(n: KGNode) -> dict:
    return {"id": n.id, "name": n.qualname or n.name, "type": n.type, "path": n.path, "degree": n.degree}


def questions(info: dict) -> list[str]:
    out = []
    for n in info["hubs"][:3]:
        out.append(f"What does `{n.qualname or n.name}` do, and what depends on it?")
    labels = {c["id"]: c["label"] for c in info["communities"]}
    for s in info["surprising"][:2]:
        out.append(
            f"Why does `{s['src'].name}` ({labels.get(s['a'], 'one area')}) connect to `{s['dst'].name}` ({labels.get(s['b'], 'another')})?"
        )
    if info["hubs"]:
        out.append(f"Where is `{info['hubs'][0].qualname or info['hubs'][0].name}` documented?")
    return out[:6]


def report(db: Session, repo: Repository) -> dict:
    """The start-here report: shape of the repository, hubs, surprising links and good first questions."""
    info = analyse(db, repo)
    labels = {c["id"]: c["label"] for c in info["communities"]}
    surprising = [
        {
            "source": _ref(s["src"]),
            "target": _ref(s["dst"]),
            "relation": s["edge"].rel,
            "trust": trust(s["edge"].provenance, s["edge"].weight),
            "from_area": labels.get(s["a"]),
            "to_area": labels.get(s["b"]),
        }
        for s in info["surprising"]
    ]
    out = {
        "repo": {"id": repo.id, "name": repo.name},
        "stats": {"nodes": info["nodes"], "edges": info["edges"], "communities": len(info["communities"])},
        "communities": info["communities"][:12],
        "hubs": [{**_ref(n), "community": info["member"].get(n.id)} for n in info["hubs"]],
        "surprising": surprising,
        "questions": questions(info),
    }
    out["markdown"] = markdown(out)
    return out


def markdown(r: dict) -> str:
    lines = [
        f"# Start here: {r['repo']['name']}",
        "",
        f"{r['stats']['nodes']} files, documents and symbols, {r['stats']['edges']} connections, "
        f"{r['stats']['communities']} areas that hang together.",
        "",
        "## Areas",
    ]
    for c in r["communities"]:
        names = ", ".join(f"`{t['name']}`" for t in c["top"])
        lines.append(f"- **{c['label']}** ({c['size']} items, {c['files']} files). Key: {names or 'n/a'}")
    lines += ["", "## Hubs: change these with care", "Most connected, so a change here reaches the most other things."]
    for h in r["hubs"]:
        lines.append(f"- `{h['name']}` ({h['type']}, {h['degree']} connections) in `{h['path']}`")
    if r["surprising"]:
        lines += [
            "",
            "## Surprising connections",
            "Links between areas that are otherwise separate. Worth understanding before changing either side.",
        ]
        for s in r["surprising"]:
            lines.append(
                f"- `{s['source']['name']}` ({s['from_area']}) {s['relation'].lower().replace('_', ' ')} "
                f"`{s['target']['name']}` ({s['to_area']}), {s['trust']}"
            )
    if r["questions"]:
        lines += ["", "## Good first questions"] + [f"- {q}" for q in r["questions"]]
    return "\n".join(lines) + "\n"
