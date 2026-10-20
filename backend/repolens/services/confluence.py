"""Confluence (Cloud and Data Center) client + storage-format -> Markdown conversion.

Read-only: only GET requests are ever made. Credentials come from the organisation's encrypted connection.
"""

import html
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote_plus, urlparse

import httpx

from .jira import JiraError, validate_base_url


class ConfluenceError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


# ------------------------------------------------------------------------------------------------ URL parsing
@dataclass
class ConfluenceRef:
    base_url: str  # scheme://host[/context]  (no /wiki)
    space_key: str = ""
    page_id: str = ""
    title: str = ""


def parse_confluence_url(url: str) -> ConfluenceRef:
    """Understands Cloud (/wiki/spaces/KEY/pages/ID/Title) and Data Center (/display/KEY/Title,
    /pages/viewpage.action?pageId=ID, /spaces/viewspace.action?key=KEY) page and space URLs."""
    u = urlparse(url.strip())
    if u.scheme not in ("https", "http") or not u.hostname:
        raise ConfluenceError("Enter the full https URL of a Confluence page or space")
    origin = f"{u.scheme}://{u.netloc}"
    path = u.path
    qs = parse_qs(u.query)
    ctx = ""
    m = re.match(r"^(.*?)/(wiki|display|spaces|pages|x)(/|$)", path)
    if m:
        ctx = m.group(1) if m.group(2) != "wiki" else m.group(1)
    base = origin + ctx
    rest = path[len(ctx) :]
    rest = rest[len("/wiki") :] if rest.startswith("/wiki") else rest
    if rest.startswith("/spaces/viewspace.action") and qs.get("key"):
        return ConfluenceRef(base, qs["key"][0])
    m = re.match(r"^/spaces/([^/]+)/pages/(\d+)(?:/([^/]*))?", rest)
    if m:
        return ConfluenceRef(base, m.group(1), m.group(2), unquote_plus(m.group(3) or ""))
    m = re.match(r"^/spaces/([^/]+)(?:/(?:overview|pages)?/?)?$", rest) or re.match(r"^/spaces/([^/]+)/?", rest)
    if m and "/pages/" not in rest:
        return ConfluenceRef(base, m.group(1))
    m = re.match(r"^/display/([^/]+)(?:/([^/]+))?/?$", rest)
    if m:
        return ConfluenceRef(base, m.group(1), "", unquote_plus(m.group(2) or ""))
    if rest.startswith("/pages/viewpage.action") and qs.get("pageId"):
        return ConfluenceRef(base, "", qs["pageId"][0])
    if rest.startswith("/spaces/viewspace.action") and qs.get("key"):
        return ConfluenceRef(base, qs["key"][0])
    if rest.startswith("/x/"):
        raise ConfluenceError("Short /x/ links are not supported; open the page and copy its full address")
    raise ConfluenceError("Could not recognise this as a Confluence page or space URL")


def slugify(text: str, fallback: str = "page") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or fallback


# ------------------------------------------------------------------------------------------------ client
EXPAND = "body.storage,version,ancestors,space,history.lastUpdated"


class ConfluenceClient:
    def __init__(
        self,
        base_url: str,
        deployment: str,
        secret: str,
        email: str = "",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        try:
            base = validate_base_url(base_url)  # https only, no private addresses outside dev
        except JiraError as e:
            raise ConfluenceError(str(e)) from e
        self.deployment = deployment
        self.root = base
        if deployment == "cloud" and not urlparse(base).path.rstrip("/").endswith("/wiki"):
            self.root = base.rstrip("/") + "/wiki"
        headers = {"Accept": "application/json"}
        auth = None
        if deployment == "cloud":
            auth = (email, secret)
        else:
            headers["Authorization"] = f"Bearer {secret}"
        self.sleep = sleep
        self.http = httpx.Client(
            base_url=self.root, headers=headers, auth=auth, timeout=30, transport=transport, follow_redirects=False
        )

    # -- low level -----------------------------------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(3):
            try:
                r = self.http.get(f"/rest/api{path}", params=params)
            except httpx.HTTPError as e:
                raise ConfluenceError(f"Confluence unreachable: {type(e).__name__}") from e
            if r.status_code == 429 and attempt < 2:
                self.sleep(min(float(r.headers.get("Retry-After", "2") or 2), 10.0))
                continue
            if r.status_code >= 500 and attempt < 1:
                self.sleep(1.0)
                continue
            break
        if r.status_code == 401:
            raise ConfluenceError("Authentication failed: check the email / API token (or personal access token)", 401)
        if r.status_code == 403:
            raise ConfluenceError("The connected account has no permission to read this content", 403)
        if r.status_code == 404:
            raise ConfluenceError("Page or space not found (or not visible to the connected account)", 404)
        if r.status_code == 429:
            raise ConfluenceError("Confluence rate limit reached; try again in a few minutes", 429)
        if r.status_code >= 400:
            raise ConfluenceError(f"Confluence returned {r.status_code}", r.status_code)
        try:
            return r.json()
        except ValueError as e:
            raise ConfluenceError("Confluence returned a non-JSON response (wrong base URL or a login page?)") from e

    def _paged(self, path: str, params: dict, limit: int = 25) -> Iterator[dict]:
        start = 0
        while True:
            d = self._get(path, {**params, "limit": limit, "start": start})
            results = d.get("results", [])
            yield from results
            if len(results) < limit or not d.get("_links", {}).get("next"):
                return
            start += limit

    # -- API ---------------------------------------------------------------------------------------------------
    def current_user(self) -> dict:
        return self._get("/user/current")

    def get_page(self, page_id: str) -> dict:
        return self._get(f"/content/{page_id}", {"expand": EXPAND})

    def children(self, page_id: str) -> Iterator[dict]:
        yield from self._paged(f"/content/{page_id}/child/page", {"expand": EXPAND})

    def space_pages(self, space_key: str) -> Iterator[dict]:
        yield from self._paged("/content", {"spaceKey": space_key, "type": "page", "expand": EXPAND})

    def find_page(self, space_key: str, title: str) -> dict | None:
        d = self._get("/content", {"spaceKey": space_key, "title": title, "type": "page", "expand": EXPAND, "limit": 1})
        return (d.get("results") or [None])[0]

    def space(self, key: str) -> dict:
        return self._get(f"/space/{key}")

    def web_url(self, page: dict) -> str:
        links = page.get("_links", {})
        webui = links.get("webui", "")
        base = links.get("base") or self.root
        if webui:
            return base.rstrip("/") + webui
        return f"{self.root}/pages/viewpage.action?pageId={page.get('id', '')}"


# ------------------------------------------------------------------------------------------------ storage -> markdown
VOID = {
    "br",
    "hr",
    "img",
    "ri:page",
    "ri:attachment",
    "ri:user",
    "ri:url",
    "ri:space",
    "ri:blog-post",
    "ac:emoticon",
    "col",
}
SKIP_MACROS = {
    "toc",
    "children",
    "pagetree",
    "recently-updated",
    "contentbylabel",
    "livesearch",
    "anchor",
    "excerpt-include",
    "profile-picture",
    "attachments",
    "gallery",
    "tasks-report-macro",
    "details-summary",
    "blog-posts",
}
# structural wrappers whose children are rendered in place, and page chrome that is dropped entirely
CONTAINERS = {
    "ac:layout",
    "ac:layout-section",
    "ac:layout-cell",
    "ac:rich-text-body",
    "body",
    "root",
    "span",
    "section",
    "div",
    "p",
    "tbody",
    "thead",
    "tr",
    "td",
    "th",
    "html",
    "main",
    "article",
    "figure",
    "details",
    "summary",
}
DROPPED = {"head", "title", "meta", "link", "nav", "footer", "header", "aside", "form", "button"}
PANELS = {
    "info": "Info",
    "note": "Note",
    "warning": "Warning",
    "tip": "Tip",
    "success": "Success",
    "error": "Error",
    "panel": "Note",
    "quote": "Quote",
}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_Node | str"] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.stack[-1].children.append(_Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _text(node: "_Node | str") -> str:
    if isinstance(node, str):
        return node
    return "".join(_text(c) for c in node.children)


def _find(node: _Node, tag: str) -> _Node | None:
    for c in node.children:
        if isinstance(c, _Node):
            if c.tag == tag:
                return c
            hit = _find(c, tag)
            if hit:
                return hit
    return None


def _param(node: _Node, name: str) -> str:
    for c in node.children:
        if isinstance(c, _Node) and c.tag == "ac:parameter" and c.attrs.get("ac:name") == name:
            return _text(c).strip()
    return ""


class _Renderer:
    def inline(self, node: "_Node | str") -> str:
        if isinstance(node, str):
            return re.sub(r"\s+", " ", node)
        t = node.tag
        if t in ("script", "style", "noscript", "iframe", "object", "embed"):
            return ""  # never carry active content into the wiki
        kids = "".join(self.inline(c) for c in node.children)
        if t in ("strong", "b"):
            return f"**{kids.strip()}**" if kids.strip() else ""
        if t in ("em", "i"):
            return f"*{kids.strip()}*" if kids.strip() else ""
        if t == "code":
            return f"`{_text(node).strip()}`" if _text(node).strip() else ""
        if t in ("s", "del", "strike"):
            return f"~~{kids.strip()}~~"
        if t == "br":
            return "  \n"
        if t == "a":
            href = node.attrs.get("href", "")
            if re.match(r"^(https?://|mailto:|/|#|\.{1,2}/)", href) and kids.strip():
                return f"[{kids.strip()}]({href})"
            return kids
        if t == "ac:link":
            return self._ac_link(node)
        if t == "ac:structured-macro":
            return self._macro_inline(node)
        if t == "time":
            return node.attrs.get("datetime", kids)
        if t in ("ac:emoticon", "ac:image", "img", "ri:attachment", "ac:inline-comment-marker") and not kids:
            return ""
        if t.startswith("ri:"):
            return ""
        return kids

    def _ac_link(self, node: _Node) -> str:
        label = ""
        body = _find(node, "ac:plain-text-link-body") or _find(node, "ac:link-body")
        if body is not None:
            label = _text(body).strip()
        page = _find(node, "ri:page")
        if page is not None and page.attrs.get("ri:content-title"):
            title = page.attrs["ri:content-title"]
            anchor = node.attrs.get("ac:anchor", "")
            target = f"{title}#{anchor}" if anchor else title
            return f"[[{target}|{label}]]" if label and label != title else f"[[{target}]]"
        user = _find(node, "ri:user")
        if user is not None:
            return "@" + (label or user.attrs.get("ri:userkey", user.attrs.get("ri:account-id", "user"))[:12])
        att = _find(node, "ri:attachment")
        if att is not None:
            return label or att.attrs.get("ri:filename", "attachment")
        return label

    def _macro_inline(self, node: _Node) -> str:
        name = node.attrs.get("ac:name", "")
        if name == "jira":
            return _param(node, "key") or _param(node, "jqlQuery")
        if name == "status":
            return f"[{_param(node, 'title') or 'status'}]"
        if name in SKIP_MACROS:
            return ""
        return ""

    # -- block level ---------------------------------------------------------------------------------------
    def block(self, node: "_Node | str", depth: int = 0) -> str:
        if isinstance(node, str):
            return self.inline(node) if node.strip() else ""
        t = node.tag
        if re.fullmatch(r"h[1-6]", t):
            return f"\n\n{'#' * int(t[1])} {self.inline(node).strip()}\n\n"
        if t in ("p", "div") and not self._has_blocks(node):
            text = self.inline(node).strip()
            return f"\n\n{text}\n\n" if text else ""
        if t == "pre":
            return f"\n\n```\n{_text(node).strip(chr(10))}\n```\n\n"
        if t in ("ul", "ol"):
            return "\n" + self._list(node, depth) + "\n"
        if t == "table":
            return "\n\n" + self._table(node) + "\n\n"
        if t == "blockquote":
            inner = self.children(node, depth).strip()
            return "\n\n" + "\n".join("> " + ln if ln.strip() else ">" for ln in inner.splitlines()) + "\n\n"
        if t == "hr":
            return "\n\n---\n\n"
        if t == "ac:structured-macro":
            return self._macro_block(node, depth)
        if t == "ac:task-list":
            return (
                "\n\n"
                + "\n".join(self._task(c) for c in node.children if isinstance(c, _Node) and c.tag == "ac:task")
                + "\n\n"
            )
        if t in DROPPED:
            return ""
        if t in CONTAINERS:
            return self.children(node, depth)
        text = self.inline(node)
        return text if text.strip() else ""

    def _has_blocks(self, node: _Node) -> bool:
        return any(
            isinstance(c, _Node)
            and (
                re.fullmatch(r"h[1-6]|ul|ol|table|pre|blockquote|div|p|hr", c.tag)
                or c.tag == "ac:structured-macro"
                and c.attrs.get("ac:name") in {*PANELS, "code", "noformat", "expand", "ui-expand"}
            )
            for c in node.children
        )

    def children(self, node: _Node, depth: int = 0) -> str:
        parts: list[str] = []
        inline_run: list[_Node | str] = []

        def flush() -> None:
            text = "".join(self.inline(c) for c in inline_run).strip()
            if text:
                parts.append(f"\n\n{text}\n\n")
            inline_run.clear()

        for c in node.children:
            if isinstance(c, str) or (isinstance(c, _Node) and not self._is_block(c)):
                inline_run.append(c)
            else:
                flush()
                parts.append(self.block(c, depth))
        flush()
        return "".join(parts)

    def _is_block(self, n: _Node) -> bool:
        if n.tag == "ac:structured-macro":
            return n.attrs.get("ac:name") not in ("jira", "status", "anchor")
        return (
            n.tag in CONTAINERS
            or n.tag in DROPPED
            or bool(re.fullmatch(r"h[1-6]|ul|ol|table|pre|blockquote|hr|ac:task-list", n.tag))
        )

    def _macro_block(self, node: _Node, depth: int) -> str:
        name = node.attrs.get("ac:name", "")
        if name in SKIP_MACROS:
            return ""
        if name in ("code", "noformat"):
            body = _find(node, "ac:plain-text-body")
            lang = _param(node, "language") if name == "code" else ""
            return f"\n\n```{lang}\n{(_text(body) if body is not None else '').strip(chr(10))}\n```\n\n"
        rich = _find(node, "ac:rich-text-body")
        inner = self.children(rich, depth).strip() if rich is not None else ""
        if name in PANELS:
            label = _param(node, "title") or PANELS[name]
            lines = [f"**{label}:**", *([inner] if inner else [])]
            return "\n\n" + "\n".join("> " + ln if ln.strip() else ">" for ln in "\n".join(lines).splitlines()) + "\n\n"
        if name in ("expand", "ui-expand"):
            title = _param(node, "title")
            return f"\n\n**{title}**\n\n{inner}\n\n" if title else f"\n\n{inner}\n\n"
        if name in ("jira", "status"):
            return f"\n\n{self._macro_inline(node)}\n\n"
        return f"\n\n{inner}\n\n" if inner else ""

    def _list(self, node: _Node, depth: int) -> str:
        ordered = node.tag == "ol"
        lines: list[str] = []
        n = 0
        for li in node.children:
            if not isinstance(li, _Node) or li.tag != "li":
                continue
            n += 1
            own = "".join(
                self.inline(c) for c in li.children if not (isinstance(c, _Node) and c.tag in ("ul", "ol"))
            ).strip()
            marker = f"{n}." if ordered else "-"
            lines.append("  " * depth + f"{marker} {own}")
            for sub in li.children:
                if isinstance(sub, _Node) and sub.tag in ("ul", "ol"):
                    lines.append(self._list(sub, depth + 1))
        return "\n".join(line for line in lines if line.strip())

    def _task(self, task: _Node) -> str:
        done = _text(_find(task, "ac:task-status") or "").strip() == "complete"
        body = _find(task, "ac:task-body")
        text = self.inline(body).strip() if body is not None else ""
        return f"- [{'x' if done else ' '}] {text}"

    def _table(self, node: _Node) -> str:
        rows: list[list[str]] = []
        header = False

        def collect(n: _Node) -> None:
            nonlocal header
            for c in n.children:
                if not isinstance(c, _Node):
                    continue
                if c.tag == "tr":
                    cells = [c2 for c2 in c.children if isinstance(c2, _Node) and c2.tag in ("th", "td")]
                    if not rows and cells and all(x.tag == "th" for x in cells):
                        header = True
                    rows.append([self._cell(x) for x in cells])
                elif c.tag in ("tbody", "thead", "colgroup"):
                    collect(c)

        collect(node)
        rows = [r for r in rows if r]
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        head, body = (rows[0], rows[1:]) if header else ([f"Column {i + 1}" for i in range(width)], rows)
        out = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
        out += ["| " + " | ".join(r) + " |" for r in body]
        return "\n".join(out)

    def _cell(self, cell: _Node) -> str:
        text = self.children(cell).strip() or "".join(self.inline(c) for c in cell.children).strip()
        return re.sub(r"\s*\n+\s*", " ", text).replace("|", "\\|")


MAX_MARKDOWN = 300_000


def storage_to_markdown(storage: str) -> str:
    """Convert Confluence storage format (XHTML + ac:/ri: macros) to Markdown. Unknown macros degrade to their text."""
    cdata = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: html.escape(m.group(1), quote=False), storage or "", flags=re.S)
    parser = _TreeBuilder()
    parser.feed(cdata)
    parser.close()
    md = _Renderer().block(parser.root)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md[:MAX_MARKDOWN] + ("\n\n_[content truncated]_" if len(md) > MAX_MARKDOWN else "")
