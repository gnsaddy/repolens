"""Notion pages (internal integration token) as searchable Markdown. Only pages shared with the integration are read."""

import re
import time
from typing import Any

import httpx

from . import Connector, ConnectorError, Ctx, FetchResult, Field, RemoteDoc, register, transports
from .base import epoch, front_matter, parse_ts, slug

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MAX_BLOCKS = 600
MAX_DEPTH = 4
UUID = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def page_id_from(value: str) -> str:
    """A bare id, or the 32 hex characters at the end of a notion.so URL."""
    m = list(UUID.finditer(value.strip()))
    if not m:
        raise ConnectorError("That does not contain a Notion page id")
    return m[-1].group(0).replace("-", "")


def rich(parts: list[dict]) -> str:
    out = []
    for t in parts or []:
        raw = t.get("plain_text", "")
        core = raw.strip()
        if not core:
            out.append(raw)
            continue
        lead, trail = raw[: len(raw) - len(raw.lstrip())], raw[len(raw.rstrip()) :]
        a = t.get("annotations", {})
        if a.get("code"):
            core = f"`{core}`"
        if a.get("bold"):
            core = f"**{core}**"
        if a.get("italic"):
            core = f"*{core}*"
        if a.get("strikethrough"):
            core = f"~~{core}~~"
        href = t.get("href")
        if href and href.startswith(("http://", "https://")):
            core = f"[{core}]({href})"
        out.append(lead + core + trail)
    return "".join(out)


def blocks_to_md(blocks: list[dict], children_of, depth: int = 0) -> str:
    """children_of(block) -> list of child blocks (fetched lazily)."""
    lines: list[str] = []
    num = 0
    pad = "  " * depth
    for b in blocks:
        t = b.get("type", "")
        data = b.get(t, {}) if isinstance(b.get(t), dict) else {}
        text = rich(data.get("rich_text", []))
        num = num + 1 if t == "numbered_list_item" else 0
        kids = ""
        if b.get("has_children") and depth < MAX_DEPTH and t not in ("child_page", "child_database", "table"):
            kids = blocks_to_md(children_of(b), children_of, depth + 1)
        if t in ("heading_1", "heading_2", "heading_3"):
            lines.append(f"{'#' * (int(t[-1]) + 1)} {text}\n")
        elif t == "paragraph":
            lines.append(f"{pad}{text}\n" if text else "")
        elif t == "bulleted_list_item":
            lines.append(f"{pad}- {text}")
        elif t == "numbered_list_item":
            lines.append(f"{pad}{num}. {text}")
        elif t == "to_do":
            lines.append(f"{pad}- [{'x' if data.get('checked') else ' '}] {text}")
        elif t == "toggle":
            lines.append(f"{pad}- {text}")
        elif t == "quote":
            lines.append(f"> {text}\n")
        elif t == "callout":
            lines.append(f"> {(data.get('icon') or {}).get('emoji', '')} {text}".replace("  ", " ") + "\n")
        elif t == "code":
            lines.append(
                f"```{data.get('language', '') if data.get('language') != 'plain text' else ''}\n{rich(data.get('rich_text', []))}\n```\n"
            )
        elif t == "divider":
            lines.append("---\n")
        elif t == "table":
            rows = children_of(b)
            cells = [[rich(c) for c in r.get("table_row", {}).get("cells", [])] for r in rows]
            if cells:
                lines.append("")
                lines.append("| " + " | ".join(cells[0]) + " |")
                lines.append("|" + "---|" * len(cells[0]))
                lines.extend("| " + " | ".join(r) + " |" for r in cells[1:])
                lines.append("")
        elif t == "child_page":
            lines.append(f"- [[{data.get('title', 'Untitled')}]]")
        elif t in ("bookmark", "link_preview", "embed"):
            url = data.get("url", "")
            if url.startswith(("http://", "https://")):
                lines.append(f"[{rich(data.get('caption', [])) or url}]({url})\n")
        elif t in ("image", "file", "pdf", "video"):
            cap = rich(data.get("caption", []))
            lines.append(f"*({t}{': ' + cap if cap else ''})*\n")
        elif t == "equation":
            lines.append(f"`{data.get('expression', '')}`\n")
        elif text:
            lines.append(text + "\n")
        if kids:
            lines.append(kids)
    return "\n".join(lines).strip("\n") + ("\n" if lines else "")


class NotionSource(Connector):
    kind = "notion"
    label = "Notion"
    description = "Import pages shared with an internal integration. Create one at notion.so/my-integrations and share pages with it."
    fields = [
        Field(
            "root_page",
            "Start from a page",
            required=False,
            placeholder="https://www.notion.so/Team-wiki-0123456789abcdef0123456789abcdef",
            help="Leave empty to import every page shared with the integration.",
        ),
        Field("token", "Integration token", type="password", secret=True, placeholder="secret_… or ntn_…"),
    ]

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        root = str(config.get("root_page", "")).strip()
        return {"root_page": page_id_from(root) if root else ""}

    def describe(self, config: dict[str, Any]) -> str:
        return "Notion" + (f" · page {config['root_page'][:8]}…" if config.get("root_page") else " · shared pages")

    def _client(self, ctx: Ctx) -> httpx.Client:
        if len(ctx.secret) < 20:
            raise ConnectorError("That does not look like a Notion integration token")
        return httpx.Client(
            base_url=API,
            headers={"Authorization": f"Bearer {ctx.secret}", "Notion-Version": VERSION},
            timeout=30,
            transport=ctx.transport or transports.get("notion"),
        )

    @staticmethod
    def _call(c: httpx.Client, method: str, path: str, **kw) -> dict:
        for attempt in range(2):
            try:
                r = c.request(method, path, **kw)
            except httpx.HTTPError as e:
                raise ConnectorError(f"Notion is unreachable ({type(e).__name__})") from e
            if r.status_code == 429 and attempt == 0:
                time.sleep(min(int(r.headers.get("Retry-After", "1")), 5))
                continue
            break
        if r.status_code in (401, 403):
            raise ConnectorError(
                "Notion rejected the token. Check it and that the pages are shared with the integration."
            )
        if r.status_code == 404:
            raise ConnectorError(
                "Notion: page not found. Share it with the integration (Connections menu on the page)."
            )
        if r.status_code >= 400:
            raise ConnectorError(f"Notion returned {r.status_code}")
        return r.json()

    def check(self, ctx: Ctx) -> str:
        with self._client(ctx) as c:
            me = self._call(c, "GET", "/users/me")
        ws = (me.get("bot") or {}).get("workspace_name") or ""
        return f"Connected to Notion{' workspace ' + ws if ws else ''} as {me.get('name') or 'integration'}"

    def _pages(self, c: httpx.Client, root: str, cap: int) -> tuple[list[dict], bool]:
        pages: list[dict] = []
        if root:
            frontier = [root]
            seen = set()
            while frontier and len(pages) < cap:
                pid = frontier.pop(0)
                if pid in seen:
                    continue
                seen.add(pid)
                try:
                    pages.append(self._call(c, "GET", f"/pages/{pid}"))
                except ConnectorError:
                    if not pages:
                        raise  # the starting page itself must be reachable
                    continue  # a sub-page that is not shared with the integration is skipped
                for b in self._children(c, pid):
                    if b.get("type") == "child_page":
                        frontier.append(b["id"].replace("-", ""))
            return pages, bool(frontier)
        cursor = None
        while len(pages) < cap:
            body: dict[str, Any] = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            d = self._call(c, "POST", "/search", json=body)
            pages.extend(d.get("results", []))
            cursor = d.get("next_cursor")
            if not d.get("has_more"):
                return pages, False
        return pages[:cap], True

    def _children(self, c: httpx.Client, block_id: str) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while len(out) < MAX_BLOCKS:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            d = self._call(c, "GET", f"/blocks/{block_id}/children", params=params)
            out.extend(d.get("results", []))
            cursor = d.get("next_cursor")
            if not d.get("has_more"):
                break
        return out

    def fetch(self, ctx: Ctx) -> FetchResult:
        docs: list[RemoteDoc] = []
        with self._client(ctx) as c:
            pages, truncated = self._pages(c, ctx.config.get("root_page", ""), ctx.max_docs)
            by_id = {p["id"].replace("-", ""): p for p in pages}

            def title_of(p: dict) -> str:
                for prop in (p.get("properties") or {}).values():
                    if prop.get("type") == "title":
                        return rich(prop.get("title", [])) or "Untitled"
                return "Untitled"

            def path_of(p: dict, guard: int = 0) -> str:
                par = ((p.get("parent") or {}).get("page_id") or "").replace("-", "")
                name = slug(title_of(p), "untitled")
                if par in by_id and guard < 10:
                    base = path_of(by_id[par], guard + 1)[: -len(".md")]
                    return f"{base}/{name}.md"
                return f"notion/{name}.md"

            budget = [MAX_BLOCKS * 3]

            def kids(block: dict) -> list[dict]:
                if budget[0] <= 0:
                    return []
                got = self._children(c, block["id"])
                budget[0] -= len(got)
                return got

            for p in pages:
                pid = p["id"].replace("-", "")
                title = title_of(p)
                budget[0] = MAX_BLOCKS
                body = blocks_to_md(self._children(c, pid), kids, 0)
                updated = parse_ts(p.get("last_edited_time"))
                par = ((p.get("parent") or {}).get("page_id") or "").replace("-", "")
                url = p.get("url", "")
                md = front_matter(title, "notion", url) + f"# {title}\n\n{body}"
                docs.append(
                    RemoteDoc(
                        pid,
                        path_of(p),
                        title,
                        md,
                        url,
                        epoch(updated),
                        updated,
                        path_of(by_id[par]) if par in by_id else "",
                    )
                )
        return FetchResult(docs, truncated)


register(NotionSource())
