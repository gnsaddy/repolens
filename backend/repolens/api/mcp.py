import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..services import mcp_server

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.post("")
async def mcp(request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Model Context Protocol endpoint (JSON-RPC over HTTP). Authenticate with a personal access token."""
    try:
        payload = json.loads(await request.body())
    except ValueError:
        return Response(
            json.dumps(mcp_server._error(None, -32700, "Parse error")), media_type="application/json", status_code=400
        )
    batch = isinstance(payload, list)
    replies = [r for m in (payload if batch else [payload]) if (r := mcp_server.handle(db, p, m)) is not None]
    if not replies:
        return Response(status_code=202)  # only notifications: acknowledged, nothing to say
    body = replies if batch else replies[0]
    return Response(json.dumps(body, default=str), media_type="application/json")


@router.get("")
def no_stream(p: Principal = Depends(current_principal)):
    """RepoLens does not push server messages, so there is no event stream to open."""
    raise HTTPException(405, "This server replies to POST requests only", headers={"Allow": "POST"})
