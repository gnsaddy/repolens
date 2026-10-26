"""A Model Context Protocol server: lets an editor or agent (Claude Code, Cursor, ...) ask RepoLens about your code, docs and pull
requests with the person's own access.

Tools call the same endpoint functions the web app uses, in-process and with the caller's own principal, so every permission,
repository restriction and audit rule applies unchanged. JSON-RPC 2.0 over HTTP POST (the "streamable HTTP" transport, replying
with plain JSON); `devtools/mcp_stdio.py` bridges editors that only speak stdio."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import __version__
from ..deps import Principal
from ..services.policy import authorize

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 9000


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[[Session, Principal, dict], str]
    write: bool = False  # needs a token (or sign-in) that may start work


def _clip(text: str, n: int = MAX_TEXT) -> str:
    return text if len(text) <= n else text[:n] + f"\n… (cut at {n} characters; narrow the request for the rest)"


def _fmt(d: Any) -> str:
    return json.dumps(d, indent=2, default=str, ensure_ascii=False)


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


S = {"type": "string"}


# ------------------------------------------------------------------------------------------------ tools
def t_repos(db: Session, p: Principal, a: dict) -> str:
    from ..api import repos as api

    rows = api.list_repos(p=p, db=db)
    if not rows:
        return "No repositories or documentation collections are visible to you."
    return "\n".join(
        f"- {r.name} (id {r.id}): {r.source}, {r.status}, {r.file_count} files, {r.chunk_count} chunks" for r in rows
    )


def t_search(db: Session, p: Principal, a: dict) -> str:
    from ..api import knowledge as api
    from ..schemas import KnowledgeQuery

    kind = a.get("kind")
    body = KnowledgeQuery(
        query=a["query"],
        repo_ids=[a["repo_id"]] if a.get("repo_id") else [],
        k=min(int(a.get("limit", 8)), 15),
        kinds=[kind] if kind else [],
    )
    hits = api.knowledge_search(body, p=p, db=db)
    if not hits:
        return "Nothing relevant was found."
    out = []
    for h in hits:
        where = f"{h['repo']}:{h['file']}:{h['start_line']}-{h['end_line']}"
        out.append(f"### {h['title'] or h['file']}  [{h['kind']}] {where}\n{h['content'][:600].strip()}")
    return _clip("\n\n".join(out))


def t_ask(db: Session, p: Principal, a: dict) -> str:
    from ..api import knowledge as api
    from ..schemas import KnowledgeQuery

    body = KnowledgeQuery(query=a["question"], repo_ids=[a["repo_id"]] if a.get("repo_id") else [], k=8, kinds=[])
    r = api.knowledge_ask(body, p=p, db=db)
    srcs = "\n".join(
        f"[{s['id']}] {s['repo']}:{s['file']}:{s['start_line']}-{s['end_line']}  {s['title']}" for s in r["sources"]
    )
    note = {
        "llm": "",
        "extractive": "(No language model is connected: these are the most relevant passages, not a generated answer.)\n",
        "none": "",
    }[r["mode"]]
    return _clip(f"{note}{r['answer']}\n\nSources:\n{srcs}" if srcs else r["answer"])


def t_find_symbol(db: Session, p: Principal, a: dict) -> str:
    from ..api import knowledge as api

    rows = api.graph_nodes(q=a["name"], type=a.get("type"), limit=10, p=p, db=db)
    if not rows:
        return "No symbol or file matches."
    return "\n".join(
        f"- {r['qualname'] or r['name']} [{r['type']}] {r['repo']}:{r['path']}:{r['start_line']}  (node_id {r['id']}, {r['degree']} connections)"
        for r in rows
    )


def t_brief(db: Session, p: Principal, a: dict) -> str:
    from ..api import knowledge as api

    b = api.graph_brief(a["node_id"], p=p, db=db)
    names = lambda xs: ", ".join(x["qualname"] or x["name"] for x in xs) or "none"  # noqa: E731
    return _clip(
        f"{b['summary']}\nUsed by ({b['callers_total']}): {names(b['callers'])}\nDepends on: {names(b['dependencies'])}\n"
        f"Documented in: {names(b['documented_in'])}\nRecent history: {names(b['history'])}\n"
        + ("Warnings:\n" + "\n".join(f"- {n}" for n in b["notes"]) if b["notes"] else "No warnings.")
    )


def t_graph_overview(db: Session, p: Principal, a: dict) -> str:
    from ..api import knowledge as api

    return _clip(api.graph_report(repo_id=a["repo_id"], p=p, db=db)["markdown"])


def t_graph_neighbors(db: Session, p: Principal, a: dict) -> str:
    from ..api import knowledge as api

    d = api.graph_node(a["node_id"], depth=1, rels=a.get("relation"), p=p, db=db)
    me = d["node"]["id"]
    by_id = {n["id"]: n for n in d["nodes"]}
    lines = []
    for e in d["edges"]:
        other = by_id.get(e["target"] if e["source"] == me else e["source"])
        if other:
            arrow = "->" if e["source"] == me else "<-"
            lines.append(
                f"- {arrow} {e['rel']} {other['qualname'] or other['name']} [{other['type']}] "
                f"{other['path']}:{other['start_line']} ({e['trust']}, node_id {other['id']})"
            )
    head = f"{d['node']['qualname'] or d['node']['name']} [{d['node']['type']}], {d['node']['degree']} connections"
    return _clip(head + "\n" + ("\n".join(lines[:40]) if lines else "No connections."))


def t_graph_path(db: Session, p: Principal, a: dict) -> str:
    from ..api import knowledge as api

    r = api.graph_path(src=a["from_id"], dst=a["to_id"], p=p, db=db)
    if not r["found"]:
        return "No connection found within 6 steps."
    out = []
    for s in r["steps"]:
        n, e = s["node"], s["edge"]
        out.append(
            (f"  {e['rel']} ({e['trust']})\n" if e else "") + f"{n['qualname'] or n['name']} [{n['type']}] {n['path']}"
        )
    return _clip("\n".join(out))


def t_wiki_page(db: Session, p: Principal, a: dict) -> str:
    from ..api import wiki as api

    page = api.get_page(repo_id=a["repo_id"], path=a["path"], p=p, db=db)
    ins = page.get("insights") or {}
    head = f"# {page['title']}  ({ins.get('kind_label', 'Document')}, {page['repo']}:{page['path']})\n"
    return _clip(head + page["markdown"])


def t_who(db: Session, p: Principal, a: dict) -> str:
    from ..api import ownership as api

    rows = api.experts(repo_id=a["repo_id"], paths=",".join(a["paths"]), p=p, db=db)
    if not rows:
        return "No history to say who maintains these paths."
    return "\n".join(
        f"- {r['author']}: {round(r['share'] * 100)}% of recent work, {r['commits']} commits" for r in rows
    )


def t_risk(db: Session, p: Principal, a: dict) -> str:
    from ..api.repos import _get_repo
    from ..services import gitops
    from ..services import risk as oracle
    from ..services.access import scope_for

    repo = _get_repo(db, p, a["repo_id"])
    try:
        if a.get("pr_number"):
            ci = oracle.from_pr(db, repo, int(a["pr_number"]))
        elif a.get("base") and a.get("head"):
            ci = oracle.from_range(repo, a["base"], a["head"])
        elif a.get("sha"):
            ci = oracle.from_commit(db, repo, a["sha"])
        else:
            return "Give a sha, a pr_number, or base and head."
    except (gitops.GitError, ValueError) as e:
        return f"Could not assess that change: {e}"
    r = oracle.assess(db, ci, scope_for(db, p))
    top = sorted(r["signals"], key=lambda s: -s["points"])[:4]
    lines = [f"Risk {round(r['score'])}/100 ({r['level']}) for {r['title'] or r['ref']}"] + [
        f"- {s['label']}: {s['detail']}" for s in top
    ]
    return _clip("\n".join(lines))


def t_investigate(db: Session, p: Principal, a: dict) -> str:
    from ..api import investigations as api
    from ..schemas import InvestigationCreate

    inv = api.start(
        InvestigationCreate(query=a["description"], repo_ids=a.get("repo_ids", []), title=a.get("title")), p=p, db=db
    )
    return f"Started investigation {inv.id}. Check it with get_investigation (it usually finishes within a minute)."


def t_get_investigation(db: Session, p: Principal, a: dict) -> str:
    from ..api import investigations as api

    inv = api.get_inv(db, p, a["id"])
    out = [f"Investigation {inv.id}: {inv.title}", f"Status: {inv.status}" + (f" ({inv.error})" if inv.error else "")]
    if inv.status == "completed":
        out += [
            f"Root cause ({inv.root_cause_status}, confidence {round((inv.confidence or 0) * 100)}%): {inv.root_cause}",
            f"Suggested fix: {inv.suggested_fix}",
        ]
        for h in inv.hypotheses[:3]:
            ev = [
                f"{e.get('repo')}:{e.get('file')}:{e.get('start_line')}" for e in h.evidence if e.get("type") == "code"
            ][:3]
            out.append(f"- {h.title} ({round(h.confidence * 100)}%) {', '.join(ev)}")
    return _clip("\n".join(out))


TOOLS: list[Tool] = [
    Tool(
        "list_repositories",
        "List the repositories and documentation collections you can see, with their ids.",
        _obj({}),
        t_repos,
    ),
    Tool(
        "search",
        "Search code, documents, pull requests and commit history by meaning and exact terms. Returns cited passages with file and line.",
        _obj(
            {
                "query": S,
                "kind": {"type": "string", "enum": ["doc", "code", "pr"]},
                "repo_id": S,
                "limit": {"type": "integer", "minimum": 1, "maximum": 15},
            },
            ["query"],
        ),
        t_search,
    ),
    Tool(
        "ask",
        "Ask a question about the codebase and its documentation. Answers cite their sources; without a connected language model it returns the most relevant passages.",
        _obj({"question": S, "repo_id": S}, ["question"]),
        t_ask,
    ),
    Tool(
        "find_symbol",
        "Find functions, classes and files by name. Returns node ids for symbol_brief.",
        _obj({"name": S, "type": {"type": "string", "enum": ["function", "class", "file", "method"]}}, ["name"]),
        t_find_symbol,
    ),
    Tool(
        "symbol_brief",
        "What to know before changing a symbol: who uses it, what it depends on, where it is documented, recent history, and warnings.",
        _obj({"node_id": S}, ["node_id"]),
        t_brief,
    ),
    Tool(
        "graph_overview",
        "A start-here map of a repository: the areas that hang together, the most connected hubs (change with care), surprising links between areas, and good first questions.",
        _obj({"repo_id": S}, ["repo_id"]),
        t_graph_overview,
    ),
    Tool(
        "graph_neighbors",
        "What a file, function, class or document is directly connected to, with each link marked fact, inferred or uncertain. Use node ids from find_symbol.",
        _obj(
            {"node_id": S, "relation": {"type": "string", "description": "Optional, e.g. CALLS, IMPORTS, MENTIONS"}},
            ["node_id"],
        ),
        t_graph_neighbors,
    ),
    Tool(
        "graph_path",
        "The shortest chain of connections between two nodes, each step marked fact, inferred or uncertain.",
        _obj({"from_id": S, "to_id": S}, ["from_id", "to_id"]),
        t_graph_path,
    ),
    Tool(
        "read_wiki_page",
        "Read a wiki or documentation page in full.",
        _obj({"repo_id": S, "path": S}, ["repo_id", "path"]),
        t_wiki_page,
    ),
    Tool(
        "who_to_ask",
        "Who maintains these files, from commit history.",
        _obj(
            {"repo_id": S, "paths": {"type": "array", "items": S, "minItems": 1, "maxItems": 20}}, ["repo_id", "paths"]
        ),
        t_who,
    ),
    Tool(
        "change_risk",
        "Score the risk of a commit, pull request or branch range, with the reasons.",
        _obj({"repo_id": S, "sha": S, "pr_number": {"type": "integer"}, "base": S, "head": S}, ["repo_id"]),
        t_risk,
    ),
    Tool(
        "get_investigation",
        "Read the result of an investigation: root cause, confidence, suggested fix and top evidence.",
        _obj({"id": S}, ["id"]),
        t_get_investigation,
    ),
    Tool(
        "investigate",
        "Start a root-cause investigation from a bug description or stack trace. Needs a write token. Read the result with get_investigation.",
        _obj({"description": S, "repo_ids": {"type": "array", "items": S}, "title": S}, ["description"]),
        t_investigate,
        write=True,
    ),
]
BY_NAME = {t.name: t for t in TOOLS}


def _tool_list(p: Principal) -> list[dict]:
    can_write = p.role != "viewer"
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.schema}
        for t in TOOLS
        if can_write or not t.write
    ]


def _error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _call(db: Session, p: Principal, params: dict) -> dict:
    tool = BY_NAME.get(params.get("name", ""))
    if tool is None:
        return {"content": [{"type": "text", "text": f"Unknown tool: {params.get('name')}"}], "isError": True}
    args = params.get("arguments") or {}
    missing = [k for k in tool.schema["required"] if k not in args]
    if missing:
        return {
            "content": [{"type": "text", "text": f"Missing required argument(s): {', '.join(missing)}"}],
            "isError": True,
        }
    try:
        authorize(
            db, p, "mcp.investigate" if tool.write else "mcp.read", tool.name
        )  # who called which tool is recorded, not what they asked
        text = tool.run(db, p, args)
        return {"content": [{"type": "text", "text": text}]}
    except HTTPException as e:
        return {"content": [{"type": "text", "text": f"{e.detail}"}], "isError": True}
    except (KeyError, TypeError, ValueError) as e:
        return {"content": [{"type": "text", "text": f"Invalid arguments: {e}"}], "isError": True}


def handle(db: Session, p: Principal, msg: dict) -> dict | None:
    """One JSON-RPC message in, the reply out (None for notifications, which get no reply)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    id_, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:  # a notification
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(
            id_,
            {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "repolens", "version": __version__},
                "instructions": "RepoLens answers questions about this team's code, documentation, pull requests and tickets with cited sources. "
                "Prefer ask or search before guessing; use symbol_brief before suggesting a change to a function.",
            },
        )
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": _tool_list(p)})
    if method == "tools/call":
        return _result(id_, _call(db, p, params))
    return _error(id_, -32601, f"Method not found: {method}")
