"""Edge protections for the API: security headers, request size limit and rate limits."""

import hashlib
import json
import logging
import re
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_settings
from .services import ratelimit, sysmetrics

_EXEMPT = ("/healthz", "/readyz", "/metrics")
# polled by the UI every few seconds: logged only when something goes wrong
_QUIET = ("/healthz", "/readyz", "/metrics", "/api/metrics/system", "/api/wiki/drift/count")
access_log = logging.getLogger("repolens.access")
_AUTH = re.compile(r"^/api/auth/(login|register|reset|sso/(discover|start|exchange))$")
# calls that spend tokens, clone repositories or fan out work
_HEAVY = re.compile(
    r"^/api/(mcp|knowledge/(ask|search|similar)|wiki/(ask|similar|search|import-url|confluence/import|connectors/[^/]+/import)"
    r"|investigations(/ci/start|/[^/]+/rerun)?|jira/(investigate|issues/[^/]+/analyze)|repositories(/[^/]+/(index|reindex))?"
    r"|risk/assess|wiki/proposals/from-(drift|incident)[^ ]*)$"
)

HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]


def _client(scope: Scope) -> str:
    from fastapi import Request

    from .services.clientip import client_ip

    return client_ip(Request(scope))


def _json(status: int, detail: str, extra: list[tuple[bytes, bytes]] | None = None) -> tuple[Message, Message]:
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *HEADERS]
    return {"type": "http.response.start", "status": status, "headers": headers + (extra or [])}, {
        "type": "http.response.body",
        "body": body,
    }


class EdgeGuard:
    """Pure ASGI so streaming responses (server-sent events) are untouched."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        s = get_settings()
        path = scope["path"]

        async def reply(status: int, detail: str, retry: int = 0) -> None:
            start, body = _json(status, detail, [(b"retry-after", str(retry).encode())] if retry else None)
            await send(start)
            await send(body)

        limit = s.max_body_mb * 1024 * 1024
        declared = next((int(v) for k, v in scope["headers"] if k == b"content-length" and v.isdigit()), 0)
        if declared > limit:
            return await reply(413, f"Request body is larger than {s.max_body_mb} MB")

        if s.rate_limit_enabled and path.startswith(("/api/", "/scim/")) and path not in _EXEMPT:
            ip = _client(scope)
            method = scope["method"]
            wait = 0
            if method == "POST" and _AUTH.match(path) or path == "/api/auth/sso/discover":
                wait = ratelimit.hit(f"auth:{ip}", s.rate_limit_auth_per_minute)
            if not wait:
                wait = ratelimit.hit(f"ip:{ip}", s.rate_limit_ip_per_minute)
            auth = next((v for k, v in scope["headers"] if k == b"authorization"), b"")
            if not wait and auth:
                who = hashlib.sha256(auth).hexdigest()[:24]
                wait = ratelimit.hit(f"user:{who}", s.rate_limit_user_per_minute)
                if not wait and method == "POST" and _HEAVY.match(path):
                    wait = ratelimit.hit(f"heavy:{who}", s.rate_limit_heavy_per_minute)
            if wait:
                return await reply(429, f"Too many requests. Try again in {wait} s.", wait)

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":  # chunked bodies have no declared length: count what arrives
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _TooLarge()
            return msg

        began = time.monotonic()

        async def with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                ms = (time.monotonic() - began) * 1000
                status = int(msg["status"])
                if path.startswith(("/api/", "/scim/")):
                    sysmetrics.record_request(status, ms)
                if (
                    status >= 400 or path not in _QUIET
                ):  # the path only: query strings can hold what people searched for
                    access_log.info("%s %s %d %.0fms %s", scope["method"], path, status, ms, _client(scope))
                have = {k.lower() for k, _ in msg["headers"]}
                extra = [(k, v) for k, v in HEADERS if k not in have]
                if path.startswith(("/api/", "/scim/")):
                    extra.append((b"cache-control", b"no-store"))
                msg = {**msg, "headers": [*msg["headers"], *extra]}
            await send(msg)

        try:
            await self.app(scope, counted, with_headers)
        except _TooLarge:
            await reply(413, f"Request body is larger than {s.max_body_mb} MB")


class _TooLarge(Exception):
    pass
