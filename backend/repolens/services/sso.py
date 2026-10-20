"""OpenID Connect sign-in (authorization code flow with PKCE) for one organisation.

Security properties: the redirect target is fixed (no open redirect); ``state`` is random, single use and expires;
the nonce and PKCE verifier are kept on the server; the ID token's signature is checked against the provider's published
keys with an allow-list of algorithms; issuer, audience, expiry and nonce are enforced; only verified e-mail addresses
in the organisation's allowed domains sign in; users are never moved between organisations; SSO never grants admin.
"""

import base64
import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SsoConfig, SsoFlow, User
from ..security import decrypt_secret, hash_password
from .jira import JiraError, validate_base_url

FLOW_TTL = timedelta(minutes=10)
ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
SSO_ROLES = ("viewer", "member")  # a federated login can never become admin by itself

# Tests inject a mock transport; production uses the network.
transport: httpx.BaseTransport | None = None
_discovery: dict[str, tuple[float, dict]] = {}


class SsoError(Exception):
    """A message safe to show to the person signing in or to an admin configuring SSO."""


def _http() -> httpx.Client:
    return httpx.Client(timeout=15, transport=transport, follow_redirects=False)


def _check_url(url: str, what: str) -> str:
    try:
        return validate_base_url(url)
    except JiraError as e:
        raise SsoError(f"{what}: {e}") from None


def discover(issuer: str, fresh: bool = False) -> dict:
    issuer = _check_url(issuer, "Issuer URL")
    hit = _discovery.get(issuer)
    if hit and not fresh and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        with _http() as c:
            r = c.get(f"{issuer}/.well-known/openid-configuration")
        r.raise_for_status()
        doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SsoError(f"Could not read {issuer}/.well-known/openid-configuration ({type(e).__name__})") from None
    if doc.get("issuer", "").rstrip("/") != issuer:
        raise SsoError("The provider reports a different issuer than the one you entered")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if key not in doc:
            raise SsoError(f"The provider's configuration is missing {key}")
        _check_url(doc[key], key.replace("_", " "))
    _discovery[issuer] = (time.time(), doc)
    return doc


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def begin(db: Session, cfg: SsoConfig, redirect_uri: str) -> str:
    """Create a flow and return the provider URL to send the browser to."""
    doc = discover(cfg.issuer)
    cutoff = datetime.now(UTC) - FLOW_TTL
    for old in db.scalars(select(SsoFlow).where(SsoFlow.created_at < cutoff)):
        db.delete(old)
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(24), secrets.token_urlsafe(64)
    db.add(SsoFlow(id=state, org_id=cfg.org_id, nonce=nonce, code_verifier=verifier))
    db.commit()
    q = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    return f"{doc['authorization_endpoint']}{'&' if '?' in doc['authorization_endpoint'] else '?'}{urlencode(q)}"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _signing_key(jwks: dict, token: str):
    header = jwt.get_unverified_header(token)
    if header.get("alg") not in ALGS:
        raise SsoError("The ID token uses an unsupported signature algorithm")
    keys = jwks.get("keys", [])
    match = (
        [k for k in keys if k.get("kid") == header.get("kid")]
        if header.get("kid")
        else (keys if len(keys) == 1 else [])
    )
    if not match:
        raise SsoError("The ID token was signed with an unknown key")
    return jwt.PyJWK(match[0]).key


def complete(db: Session, state: str, code: str, redirect_uri: str) -> User:
    """Validate the callback, exchange the code and return the (possibly new) user."""
    flow = db.get(SsoFlow, state)
    if flow is None or flow.login_code or _aware(flow.created_at) < datetime.now(UTC) - FLOW_TTL:
        raise SsoError("This sign-in link expired or was already used. Start again.")
    cfg = db.scalar(select(SsoConfig).where(SsoConfig.org_id == flow.org_id, SsoConfig.enabled.is_(True)))
    if cfg is None:
        raise SsoError("Single sign-on is not enabled for this organisation")
    flow.login_code = "used"  # a flow can be completed once, even if validation below fails
    db.commit()
    doc = discover(cfg.issuer)
    try:
        with _http() as c:
            r = c.post(
                doc["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": cfg.client_id,
                    "client_secret": decrypt_secret(cfg.client_secret_encrypted) if cfg.client_secret_encrypted else "",
                    "code_verifier": flow.code_verifier,
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code >= 400:
                raise SsoError("The provider refused the sign-in code")
            id_token = r.json().get("id_token")
            if not id_token:
                raise SsoError("The provider did not return an ID token")
            jwks = c.get(doc["jwks_uri"]).json()
    except (httpx.HTTPError, ValueError) as e:
        raise SsoError(f"Could not reach the identity provider ({type(e).__name__})") from None
    try:
        claims = jwt.decode(
            id_token,
            _signing_key(jwks, id_token),
            algorithms=ALGS,
            audience=cfg.client_id,
            issuer=doc["issuer"],
            options={"require": ["exp", "iat", "sub"]},
            leeway=30,
        )
    except jwt.PyJWTError:
        raise SsoError("The ID token could not be verified") from None
    if claims.get("nonce") != flow.nonce:
        raise SsoError("The ID token does not belong to this sign-in")
    user = _user_for(db, cfg, claims)
    flow.login_code, flow.user_id = "", user.id  # cleared below once the SPA exchange code is minted
    return user


def mint_login_code(db: Session, state: str) -> str:
    flow = db.get(SsoFlow, state)
    assert flow is not None
    flow.login_code = secrets.token_urlsafe(32)
    db.commit()
    return flow.login_code


def redeem_login_code(db: Session, code: str) -> User:
    flow = db.scalar(select(SsoFlow).where(SsoFlow.login_code == code)) if len(code) > 20 else None
    if flow is None or _aware(flow.created_at) < datetime.now(UTC) - FLOW_TTL:
        raise SsoError("This sign-in expired. Start again.")
    user = db.get(User, flow.user_id)
    flow.login_code = "used"
    db.commit()
    if user is None or not user.active:
        raise SsoError("This account is deactivated")
    return user


def _user_for(db: Session, cfg: SsoConfig, claims: dict) -> User:
    email = str(claims.get("email", "")).strip().lower()
    if not email or "@" not in email:
        raise SsoError("The identity provider did not share an e-mail address")
    if claims.get("email_verified") is False or str(claims.get("email_verified", "")).lower() == "false":
        raise SsoError("Your e-mail address is not verified with the identity provider")
    domain = email.rsplit("@", 1)[1]
    if domain not in [d.lower() for d in cfg.allowed_domains]:
        raise SsoError("Your e-mail domain is not allowed to sign in to this organisation")
    sub = str(claims["sub"])
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        if user.org_id != cfg.org_id:
            raise SsoError("This e-mail address belongs to another organisation")
        if not user.active:
            raise SsoError("This account is deactivated")
        if user.external_id and user.external_id != sub and user.auth_source == "sso":
            raise SsoError("This account is linked to a different identity")
        user.external_id = user.external_id or sub
        db.commit()
        return user
    if not cfg.auto_provision:
        raise SsoError("Your account has not been created yet. Ask an administrator.")
    role = cfg.default_role if cfg.default_role in SSO_ROLES else "viewer"
    user = User(
        org_id=cfg.org_id,
        email=email,
        name=str(claims.get("name") or "")[:200],
        role=role,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        auth_source="sso",
        external_id=sub,
    )
    db.add(user)
    db.commit()
    return user


def parse_domains(raw) -> list[str]:
    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    out = []
    for d in items:
        d = str(d).strip().lower().lstrip("@")
        if not d:
            continue
        if not all(c.isalnum() or c in ".-" for c in d) or "." not in d or d.startswith((".", "-")):
            raise SsoError(f"'{d}' is not a valid e-mail domain")
        out.append(d)
    if not out:
        raise SsoError("List at least one allowed e-mail domain")
    return sorted(set(out))
