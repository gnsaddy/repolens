"""Architecture rules and cycles for a saved system.

A rule says one repository must not depend on another (`forbid`). It is checked against the dependencies that are
already proven (imports, URLs, manifests, shared names), so a violation always comes with the line that proves it.
Cycles are repositories that depend on each other through a chain of such links. Name-only matches never count here:
a rule must not fail because two repositories happen to use the same word."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ArchRule
from .access import Scope

MAX_RULES = 100
NOTE_MAX = 300


def proven(link: dict) -> int:
    """How many links of a pair are real dependencies (not name-only matches or guesses at a variable name)."""
    return link["strong"] + link["http"] + link["imports"] + link["declared"] + link["infra"]


def edges(links: Sequence[dict]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for link in links:
        if proven(link) > 0:
            out.setdefault(link["src"], {})[link["dst"]] = link
    return out


def cycles(links: Sequence[dict], names: dict[str, str]) -> list[dict]:
    """Groups of repositories that depend on each other, directly or through others (strongly connected parts), each
    with every dependency inside the loop and its proof. Smallest first."""
    graph = edges(links)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    found: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.get(v, {}):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            part = []
            while True:
                w = stack.pop()
                on.discard(w)
                part.append(w)
                if w == v:
                    break
            if len(part) > 1:
                found.append(sorted(part, key=lambda r: names.get(r, r)))

    for v in sorted(graph, key=lambda r: names.get(r, r)):
        if v not in index:
            visit(v)
    out = []
    for part in sorted(found, key=lambda p: (len(p), [names.get(r, r) for r in p])):
        inside = set(part)
        members = [
            {
                "src": a,
                "dst": b,
                "trust": graph[a][b]["trust"],
                "links": proven(graph[a][b]),
                "proof": (graph[a][b]["examples"] or [{}])[0].get("evidence", ""),
            }
            for a in part
            for b in sorted(graph.get(a, {}), key=lambda r: names.get(r, r))
            if b in inside
        ]
        out.append({"repos": part, "edges": members})
    return out


def rules_of(db: Session, org: str, group_id: str) -> list[ArchRule]:
    return list(
        db.scalars(
            select(ArchRule).where(ArchRule.org_id == org, ArchRule.group_id == group_id).order_by(ArchRule.created_at)
        )
    )


def report(rules: Sequence[ArchRule], links: Sequence[dict], names: dict[str, str], scope: Scope) -> dict:
    """Each rule the reader may see, with what breaks it; rules about a repository they cannot open are only counted."""
    graph = edges(links)
    shown, hidden = [], 0
    for r in rules:
        if not (scope.can(r.src_repo) and scope.can(r.dst_repo)):
            hidden += 1
            continue
        link = graph.get(r.src_repo, {}).get(r.dst_repo)
        shown.append(
            {
                "id": r.id,
                "kind": r.kind,
                "src": r.src_repo,
                "dst": r.dst_repo,
                "src_name": names.get(r.src_repo, "a removed repository"),
                "dst_name": names.get(r.dst_repo, "a removed repository"),
                "note": r.note,
                "created_by": r.created_by,
                "created_at": r.created_at,
                "broken": link is not None,
                "violation": None
                if link is None
                else {
                    "links": proven(link),
                    "trust": link["trust"],
                    "rels": link["rels"],
                    "examples": [e for e in link["examples"] if e["rel"] != "CALLS"][:3] or link["examples"][:3],
                },
            }
        )
    return {"rules": shown, "hidden_rules": hidden, "broken": sum(1 for r in shown if r["broken"])}


# --- export ------------------------------------------------------------------------------------------------------


def _label(text: str) -> str:
    return "".join(c for c in text if c not in '"[]{}()<>|\n\r`').strip()[:60] or "repository"


def export(analysis: dict, fmt: str) -> dict:
    """The repository-level picture as text another tool can read: Mermaid (pasted into a README or wiki) or JSON. Only
    proven dependencies are included; each carries how sure it is."""
    repos = analysis["repos"]
    links = [x for x in analysis["links"] if proven(x) > 0]
    if fmt == "json":
        import json

        body = {
            "repositories": [{"id": r["id"], "name": r["name"], "role": r.get("role", "")} for r in repos],
            "dependencies": [
                {"from": x["src"], "to": x["dst"], "links": proven(x), "trust": x["trust"], "kinds": x["rels"]}
                for x in links
            ],
        }
        return {"format": "json", "filename": "repolens-dependencies.json", "content": json.dumps(body, indent=2)}
    ids = {r["id"]: f"n{i}" for i, r in enumerate(repos)}
    lines = ["graph LR"] + [f'  {ids[r["id"]]}["{_label(r["name"])}"]' for r in repos]
    for x in links:
        arrow = "-->" if x["trust"] == "fact" else "-.->"
        lines.append(f'  {ids[x["src"]]} {arrow}|"{proven(x)}"| {ids[x["dst"]]}')
    lines.append("%% solid: proved by the code or a manifest, dashed: inferred; the number is how many links prove it")
    return {"format": "mermaid", "filename": "repolens-dependencies.mmd", "content": "\n".join(lines) + "\n"}
