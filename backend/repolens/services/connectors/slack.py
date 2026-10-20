"""Slack conversations as searchable knowledge: threads that solved a problem, and busy days as digests.

Two inputs share one converter: the Slack API (bot token, scheduled sync) and an uploaded Slack export (JSON files).
"""

import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx

from . import Connector, ConnectorError, Ctx, FetchResult, Field, RemoteDoc, register, transports
from .base import front_matter, slug

API = "https://slack.com/api"
SKIP_SUBTYPES = {
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "bot_add",
    "pinned_item",
}


def mrkdwn(text: str, users: dict[str, str], channels: dict[str, str]) -> str:
    """Slack markup to plain Markdown: mentions, channels, links and entities."""
    text = re.sub(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>", lambda m: "@" + users.get(m.group(1), m.group(1)), text)
    text = re.sub(
        r"<#(C[A-Z0-9]+)(?:\|([^>]*))?>", lambda m: "#" + (m.group(2) or channels.get(m.group(1), m.group(1))), text
    )
    text = re.sub(r"<(https?://[^|>\s]+)\|([^>]+)>", r"[\2](\1)", text)
    text = re.sub(r"<(https?://[^>\s]+)>", r"\1", text)
    text = re.sub(r"<!(here|channel|everyone)(?:\|[^>]*)?>", r"@\1", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _day(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), UTC).strftime("%Y-%m-%d")


def _line(m: dict, users: dict[str, str], channels: dict[str, str]) -> str:
    who = users.get(m.get("user", ""), m.get("username") or m.get("user") or "someone")
    when = datetime.fromtimestamp(float(m["ts"]), UTC).strftime("%H:%M")
    body = mrkdwn(m.get("text", ""), users, channels).strip()
    files = [f.get("name") or f.get("title") for f in m.get("files", []) if f.get("name") or f.get("title")]
    if files:
        body += f"\n  (attached: {', '.join(files)})"
    return f"**{who}** ({when} UTC): {body}"


def docs_from_messages(
    channel: str,
    messages: list[dict],
    replies: dict[str, list[dict]],
    users: dict[str, str],
    channels: dict[str, str],
    min_replies: int = 1,
    digest_min: int = 3,
    link_base: str = "",
) -> list[RemoteDoc]:
    """Threads with enough replies become their own document; the rest are grouped into per-day digests."""
    docs: list[RemoteDoc] = []
    loose: dict[str, list[dict]] = defaultdict(list)
    for m in sorted(messages, key=lambda x: float(x["ts"])):
        if m.get("subtype") in SKIP_SUBTYPES or not (m.get("text") or "").strip():
            continue
        thread = replies.get(m["ts"], [])
        # the first reply entry is the parent itself in conversations.replies
        follow = [r for r in thread if r["ts"] != m["ts"] and (r.get("text") or "").strip()]
        if len(follow) >= min_replies:
            title = re.sub(r"\s+", " ", mrkdwn(m["text"], users, channels)).strip()[:90] or "thread"
            lines = [_line(m, users, channels), *[f"  ↳ {_line(r, users, channels)}" for r in follow]]
            url = (
                f"{link_base}/archives/{channels.get('#' + channel, '')}/p{m['ts'].replace('.', '')}"
                if link_base and channels.get("#" + channel)
                else ""
            )
            latest = max(float(r["ts"]) for r in [m, *follow])
            md = (
                front_matter(f"#{channel}: {title}", "slack", url, channel=channel, date=_day(m["ts"]))
                + f"# #{channel}: {title}\n\n"
                + "\n\n".join(lines)
                + "\n"
            )
            docs.append(
                RemoteDoc(
                    f"{channel}:{m['ts']}",
                    f"slack/{slug(channel, 'channel')}/{_day(m['ts'])}-{m['ts'].replace('.', '')}.md",
                    f"#{channel}: {title}",
                    md,
                    url,
                    int(latest),
                    datetime.fromtimestamp(latest, UTC),
                )
            )
        else:
            loose[_day(m["ts"])].append(m)
    for day, ms in sorted(loose.items()):
        if len(ms) < digest_min:
            continue
        latest = max(float(m["ts"]) for m in ms)
        md = (
            front_matter(f"#{channel} on {day}", "slack", "", channel=channel, date=day)
            + f"# #{channel} on {day}\n\n"
            + "\n\n".join(_line(m, users, channels) for m in ms)
            + "\n"
        )
        docs.append(
            RemoteDoc(
                f"{channel}:digest:{day}",
                f"slack/{slug(channel, 'channel')}/{day}-digest.md",
                f"#{channel} on {day}",
                md,
                "",
                int(latest),
                datetime.fromtimestamp(latest, UTC),
            )
        )
    return docs


class SlackSource(Connector):
    kind = "slack"
    label = "Slack channels"
    description = "Turn helpful threads and busy days into searchable notes. Needs a bot token (xoxb-…) with channels:history, channels:read and users:read."
    fields = [
        Field(
            "channels",
            "Channels",
            placeholder="incidents, backend-help",
            help="Comma-separated channel names or ids. The bot must be a member of each.",
        ),
        Field("days", "How far back (days)", type="number", required=False, placeholder="90"),
        Field("token", "Bot token", type="password", secret=True, placeholder="xoxb-…"),
    ]

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        raw = config.get("channels", "")
        names = [c.strip().lstrip("#") for c in (raw.split(",") if isinstance(raw, str) else raw) if str(c).strip()]
        if not names or len(names) > 20:
            raise ConnectorError("List between 1 and 20 channels")
        if any(not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", n) for n in names):
            raise ConnectorError("Channel names may only contain letters, numbers, dots, dashes and underscores")
        try:
            days = int(config.get("days") or 90)
        except (TypeError, ValueError):
            raise ConnectorError("Days must be a number") from None
        return {"channels": names, "days": max(1, min(days, 730))}

    def describe(self, config: dict[str, Any]) -> str:
        return "Slack · " + ", ".join("#" + c for c in config.get("channels", [])[:4])

    def _client(self, ctx: Ctx) -> httpx.Client:
        if not ctx.secret.startswith(("xoxb-", "xoxp-")):
            raise ConnectorError("That does not look like a Slack token (it should start with xoxb-)")
        return httpx.Client(
            headers={"Authorization": f"Bearer {ctx.secret}"},
            timeout=30,
            transport=ctx.transport or transports.get("slack"),
        )

    @staticmethod
    def _call(c: httpx.Client, method: str, **params) -> dict:
        for attempt in range(2):
            try:
                r = c.get(f"{API}/{method}", params=params)
            except httpx.HTTPError as e:
                raise ConnectorError(f"Slack is unreachable ({type(e).__name__})") from e
            if r.status_code == 429 and attempt == 0:
                time.sleep(min(int(r.headers.get("Retry-After", "1")), 5))
                continue
            break
        if r.status_code >= 400:
            raise ConnectorError(f"Slack returned {r.status_code}")
        data = r.json()
        if not data.get("ok"):
            err = data.get("error", "unknown_error")
            hint = {
                "invalid_auth": "the token is invalid or revoked",
                "not_in_channel": "invite the bot to the channel",
                "missing_scope": f"the token lacks a scope ({data.get('needed', 'see docs')})",
                "channel_not_found": "the channel was not found",
            }.get(err, err)
            raise ConnectorError(f"Slack: {hint}")
        return data

    def check(self, ctx: Ctx) -> str:
        with self._client(ctx) as c:
            d = self._call(c, "auth.test")
        return f"Connected to Slack workspace {d.get('team', '')} as {d.get('user', 'bot')}"

    def fetch(self, ctx: Ctx) -> FetchResult:
        days = int(ctx.config.get("days", 90))
        oldest = str(time.time() - days * 86400)
        docs: list[RemoteDoc] = []
        truncated = False
        with self._client(ctx) as c:
            team = self._call(c, "auth.test")
            link_base = (team.get("url") or "").rstrip("/")
            users: dict[str, str] = {}
            try:
                cursor = ""
                while True:
                    d = self._call(c, "users.list", limit=200, cursor=cursor)
                    for u in d.get("members", []):
                        users[u["id"]] = u.get("real_name") or u.get("name") or u["id"]
                    cursor = (d.get("response_metadata") or {}).get("next_cursor", "")
                    if not cursor:
                        break
            except ConnectorError:
                pass  # names are a nicety; ids are used when the token lacks users:read
            by_name: dict[str, str] = {}
            cursor = ""
            while True:
                d = self._call(
                    c,
                    "conversations.list",
                    types="public_channel,private_channel",
                    exclude_archived="true",
                    limit=200,
                    cursor=cursor,
                )
                for ch in d.get("channels", []):
                    by_name[ch["name"]] = ch["id"]
                cursor = (d.get("response_metadata") or {}).get("next_cursor", "")
                if not cursor:
                    break
            id_to_name = {v: k for k, v in by_name.items()}
            for want in ctx.config["channels"]:
                cid = by_name.get(want) or (want if want in id_to_name else None)
                if cid is None:
                    raise ConnectorError(f"Slack: channel '{want}' was not found or the bot cannot see it")
                name = id_to_name[cid]
                messages: list[dict] = []
                cursor = ""
                while len(messages) < 2000:
                    d = self._call(c, "conversations.history", channel=cid, oldest=oldest, limit=200, cursor=cursor)
                    messages.extend(d.get("messages", []))
                    cursor = (d.get("response_metadata") or {}).get("next_cursor", "")
                    if not cursor:
                        break
                if len(messages) >= 2000:
                    truncated = True
                replies: dict[str, list[dict]] = {}
                for m in messages:
                    if m.get("reply_count", 0) >= 1:
                        replies[m["ts"]] = self._call(
                            c, "conversations.replies", channel=cid, ts=m["ts"], limit=200
                        ).get("messages", [])
                chans = {cid: name, **{f"#{name}": cid}}
                docs.extend(docs_from_messages(name, messages, replies, users, chans, link_base=link_base))
                if len(docs) >= ctx.max_docs:
                    return FetchResult(docs[: ctx.max_docs], True)
        return FetchResult(docs, truncated)


register(SlackSource())


def export_to_docs(files: list[tuple[str, str]]) -> list[RemoteDoc]:
    """Convert files from a Slack export: ``users.json``, ``channels.json`` and ``<channel>/<YYYY-MM-DD>.json``."""
    import json

    users: dict[str, str] = {}
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for path, content in files:
        parts = path.replace("\\", "/").strip("/").split("/")
        try:
            data = json.loads(content)
        except ValueError:
            raise ConnectorError(f"{path} is not valid JSON") from None
        if parts[-1] == "users.json" and isinstance(data, list):
            users.update(
                {
                    u["id"]: u.get("real_name") or u.get("name") or u["id"]
                    for u in data
                    if isinstance(u, dict) and "id" in u
                }
            )
        elif len(parts) >= 2 and parts[-1].endswith(".json") and isinstance(data, list):
            by_channel[parts[-2]].extend(m for m in data if isinstance(m, dict) and "ts" in m)
    docs: list[RemoteDoc] = []
    for channel, msgs in by_channel.items():
        replies: dict[str, list[dict]] = defaultdict(list)
        roots = []
        for m in msgs:
            if m.get("thread_ts") and m["thread_ts"] != m["ts"]:
                replies[m["thread_ts"]].append(m)
            else:
                roots.append(m)
        threads = {ts: [next((r for r in roots if r["ts"] == ts), {"ts": ts}), *rs] for ts, rs in replies.items()}
        docs.extend(docs_from_messages(channel, roots, threads, users, {}))
    return docs
