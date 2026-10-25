"""The caller's address, read so that a client cannot choose it."""

from fastapi import Request

from ..config import get_settings


def client_ip(request: Request) -> str:
    """With N trusted proxies, each of which appends the address it received from, the real client is the
    (N+1)-th entry from the right of X-Forwarded-For + the direct peer. Entries to the left are client-supplied."""
    peer = request.client.host if request.client else ""
    hops = get_settings().trusted_proxy_hops
    if hops <= 0:
        return peer
    chain = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()] + [peer]
    return chain[-(hops + 1)] if len(chain) > hops else chain[0]
