"""SCIM 2.0 (RFC 7643/7644) user provisioning, enough for Okta, Microsoft Entra ID and OneLogin.

Authenticated with a per-organisation bearer token (only its SHA-256 is stored). Deleting or deactivating a user
disables sign-in and invalidates their existing sessions immediately; their history is kept.
"""

import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import ScimToken, User
from ..security import hash_password
from ..services.policy import authorize

router = APIRouter(prefix="/scim/v2", tags=["scim"])
admin = APIRouter(prefix="/api/settings/scim", tags=["scim"])
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ROLES = ("admin", "member", "viewer")


def scim_error(status: int, detail: str, scim_type: str = "") -> JSONResponse:
    body = {"schemas": [ERR_SCHEMA], "status": str(status), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return JSONResponse(body, status_code=status, media_type="application/scim+json")


def org_from_token(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> str:
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    row = (
        db.scalar(select(ScimToken).where(ScimToken.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        if token
        else None
    )
    if row is None:
        raise HTTPException(401, "Invalid SCIM token")
    row.last_used_at = datetime.now(UTC)
    db.commit()
    return row.org_id


def _resource(u: User, request: Request) -> dict:
    name = (u.name or "").split(" ", 1)
    return {
        "schemas": [USER_SCHEMA],
        "id": u.id,
        "externalId": u.external_id or None,
        "userName": u.email,
        "active": u.active,
        "displayName": u.name,
        "name": {"formatted": u.name, "givenName": name[0], "familyName": name[1] if len(name) > 1 else ""},
        "emails": [{"value": u.email, "primary": True}],
        "roles": [{"value": u.role}],
        "meta": {
            "resourceType": "User",
            "created": u.created_at.isoformat(),
            "location": f"{str(request.base_url).rstrip('/')}/scim/v2/Users/{u.id}",
        },
    }


def _email(body: dict) -> str:
    email = str(body.get("userName") or "").strip().lower()
    for e in body.get("emails") or []:
        if e.get("primary") and e.get("value"):
            email = str(e["value"]).strip().lower()
    if "@" not in email or len(email) > 320:
        raise HTTPException(400, "userName must be an e-mail address")
    return email


def _role(body: dict, default: str = "viewer") -> str:
    for r in body.get("roles") or []:
        v = str(r.get("value", "")).lower()
        if v in ROLES:
            return v
    return default


def _name(body: dict) -> str:
    n = body.get("name") or {}
    return str(
        body.get("displayName")
        or n.get("formatted")
        or " ".join(x for x in (n.get("givenName"), n.get("familyName")) if x)
        or ""
    )[:200]


@router.get("/ServiceProviderConfig")
def service_provider_config(org: str = Depends(org_from_token)):
    return JSONResponse(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 200},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {"type": "oauthbearertoken", "name": "Bearer token", "description": "Per-organisation SCIM token"}
            ],
        },
        media_type="application/scim+json",
    )


@router.get("/ResourceTypes")
def resource_types(org: str = Depends(org_from_token)):
    return JSONResponse(
        [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "User",
                "name": "User",
                "endpoint": "/Users",
                "schema": USER_SCHEMA,
            }
        ],
        media_type="application/scim+json",
    )


@router.get("/Users")
def list_users(
    request: Request,
    filter: str = Query("", max_length=300),
    startIndex: int = Query(1, ge=1),
    count: int = Query(100, ge=0, le=200),
    org: str = Depends(org_from_token),
    db: Session = Depends(get_db),
):
    q = select(User).where(User.org_id == org).order_by(User.email)
    if filter:
        import re

        m = re.fullmatch(r'\s*(userName|externalId)\s+eq\s+"([^"]{1,320})"\s*', filter, re.I)
        if not m:
            return scim_error(
                400, "Only 'userName eq \"value\"' and 'externalId eq \"value\"' filters are supported", "invalidFilter"
            )
        col = User.email if m.group(1).lower() == "username" else User.external_id
        q = q.where(col == (m.group(2).lower() if col is User.email else m.group(2)))
    rows = list(db.scalars(q))
    page = rows[startIndex - 1 : startIndex - 1 + count]
    return JSONResponse(
        {
            "schemas": [LIST_SCHEMA],
            "totalResults": len(rows),
            "startIndex": startIndex,
            "itemsPerPage": len(page),
            "Resources": [_resource(u, request) for u in page],
        },
        media_type="application/scim+json",
    )


@router.post("/Users", status_code=201)
def create_user(request: Request, body: dict, org: str = Depends(org_from_token), db: Session = Depends(get_db)):
    email = _email(body)
    if db.scalar(select(User).where(User.email == email)):
        return scim_error(409, "A user with that userName already exists", "uniqueness")
    role = _role(body)
    u = User(
        org_id=org,
        email=email,
        name=_name(body),
        role=role,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        auth_source="scim",
        external_id=str(body.get("externalId") or "")[:200],
        active=body.get("active", True) is not False,
    )
    db.add(u)
    db.commit()
    return JSONResponse(_resource(u, request), status_code=201, media_type="application/scim+json")


def _get(db: Session, org: str, uid: str) -> User:
    u = db.get(User, uid)
    if u is None or u.org_id != org:
        raise HTTPException(404, "User not found")
    return u


@router.get("/Users/{uid}")
def get_user(uid: str, request: Request, org: str = Depends(org_from_token), db: Session = Depends(get_db)):
    return JSONResponse(_resource(_get(db, org, uid), request), media_type="application/scim+json")


@router.put("/Users/{uid}")
def replace_user(
    uid: str, request: Request, body: dict, org: str = Depends(org_from_token), db: Session = Depends(get_db)
):
    u = _get(db, org, uid)
    email = _email(body)
    if email != u.email:
        if db.scalar(select(User).where(User.email == email)):
            return scim_error(409, "A user with that userName already exists", "uniqueness")
        u.email = email
    u.name, u.external_id = _name(body), str(body.get("externalId") or u.external_id)[:200]
    u.active = body.get("active", True) is not False
    u.role = _role(body, u.role)
    db.commit()
    return JSONResponse(_resource(u, request), media_type="application/scim+json")


@router.patch("/Users/{uid}")
def patch_user(
    uid: str, request: Request, body: dict, org: str = Depends(org_from_token), db: Session = Depends(get_db)
):
    u = _get(db, org, uid)
    for op in body.get("Operations") or []:
        name = str(op.get("op", "")).lower()
        path = str(op.get("path", "")).lower()
        value = op.get("value")
        if name not in ("add", "replace", "remove"):
            return scim_error(400, f"Unsupported operation {name}", "invalidValue")
        fields = {path: value} if path else (value if isinstance(value, dict) else {})
        for key, val in fields.items():
            k = key.lower()
            if k == "active":
                u.active = str(val).lower() != "false"
            elif k in ("displayname", "name.formatted"):
                u.name = str(val)[:200]
            elif k == "externalid":
                u.external_id = str(val)[:200]
            elif k == "username":
                u.email = _email({"userName": val})
            elif k == "roles" and isinstance(val, list):
                u.role = _role({"roles": val}, u.role)
    db.commit()
    return JSONResponse(_resource(u, request), media_type="application/scim+json")


@router.delete("/Users/{uid}", status_code=204)
def delete_user(uid: str, org: str = Depends(org_from_token), db: Session = Depends(get_db)):
    """Deprovision: sign-in and tokens stop working immediately; the account and its history are kept."""
    u = _get(db, org, uid)
    u.active = False
    db.commit()


# ------------------------------------------------------------------------------------------------ admin: token
@admin.get("")
def token_status(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", "scim-view")
    row = db.scalar(select(ScimToken).where(ScimToken.org_id == p.org_id))
    return {
        "configured": row is not None,
        "created_at": row.created_at if row else None,
        "last_used_at": row.last_used_at if row else None,
    }


@admin.post("/token", status_code=201)
def create_token(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Create (or rotate) the SCIM token. The plain value is returned once and cannot be retrieved again."""
    authorize(db, p, "sso.configure", "scim-token")
    token = "scim_" + secrets.token_urlsafe(32)
    row = db.scalar(select(ScimToken).where(ScimToken.org_id == p.org_id))
    if row is None:
        row = ScimToken(org_id=p.org_id, token_hash="")
        db.add(row)
    row.token_hash, row.created_at, row.last_used_at = (
        hashlib.sha256(token.encode()).hexdigest(),
        datetime.now(UTC),
        None,
    )
    db.commit()
    return {"token": token}


@admin.delete("", status_code=204)
def revoke(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", "scim-revoke")
    row = db.scalar(select(ScimToken).where(ScimToken.org_id == p.org_id))
    if row:
        db.delete(row)
        db.commit()
