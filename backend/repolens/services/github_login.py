"""Sign in with GitHub (OAuth authorization code flow with PKCE).

GitHub is not an OpenID Connect provider, so this is not the single sign-on of `sso.py`; it borrows its flow table and
its one-time login code, and follows the same rules:

* the OAuth app is the server's (`REPOLENS_GITHUB_LOGIN_CLIENT_ID` and `_SECRET`); each workspace only says which e-mail
  domains may sign in, with which role, and whether new accounts are created;
* ``state`` is random, single use and expires, and the PKCE verifier stays on the server;
* the only addresses GitHub, never the browser, can vouch for are the person's *verified* ones; an unverified address
  is never used, because that is how accounts get taken over;
* a person signs in to the workspace whose allowed domains contain one of their verified addresses; a domain belongs to
  one workspace, so this is never ambiguous;
* users are never moved between workspaces, an existing account is linked by verified address, and signing in with
  GitHub never grants admin;
* the access token is used once to read the profile and addresses and is not stored.
"""

import hashlib
import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GithubLoginConfig, SsoFlow, User
from ..security import hash_password
from .sso import FLOW_TTL, SSO_ROLES, SsoError, _aware, _b64, mint_login_code, parse_domains, redeem_login_code

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_URL = "https://api.github.com"
FLOW_ORG = "github"  # the flow table's org column holds this: the workspace is only known after GitHub answers

# Tests inject a mock transport; production uses the network. The hosts above are fixed, never taken from settings.
transport: httpx.BaseTransport | None = None

__all__ = ["SsoError", "available", "begin", "complete", "mint_login_code", "parse_domains", "redeem_login_code"]


def _http() -> httpx.Client:
    return httpx.Client(timeout=15, transport=transport, follow_redirects=False)


def app_configured() -> bool:
    s = get_settings()
    return bool(s.github_login_client_id and s.github_login_client_secret)


def available(db: Session) -> bool:
    """Whether the sign-in page should offer GitHub: the server has an OAuth app and a workspace allows it."""
    if not app_configured():
        return False
    return db.scalar(select(GithubLoginConfig.id).where(GithubLoginConfig.enabled.is_(True)).limit(1)) is not None


def begin(db: Session, redirect_uri: str) -> str:
    """Create a flow and return the GitHub URL to send the browser to."""
    if not available(db):
        raise SsoError("Signing in with GitHub is not set up")
    cutoff = datetime.now(UTC) - FLOW_TTL
    for old in db.scalars(select(SsoFlow).where(SsoFlow.created_at < cutoff)):
        db.delete(old)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.add(SsoFlow(id=state, org_id=FLOW_ORG, nonce=FLOW_ORG, code_verifier=verifier))
    db.commit()
    query = {
        "client_id": get_settings().github_login_client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email",  # the person's addresses, and nothing about their repositories
        "state": state,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "allow_signup": "false",
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def _profile(code: str, redirect_uri: str, verifier: str) -> tuple[dict, list[str]]:
    """(the GitHub user, their verified addresses with the primary one first)."""
    s = get_settings()
    try:
        with _http() as c:
            r = c.post(
                TOKEN_URL,
                data={
                    "client_id": s.github_login_client_id,
                    "client_secret": s.github_login_client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
            token = r.json().get("access_token") if r.status_code < 400 else None
            if not token:
                raise SsoError("GitHub refused the sign-in code")
            auth = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            user = c.get(f"{API_URL}/user", headers=auth)
            emails = c.get(f"{API_URL}/user/emails", headers=auth)
            if user.status_code >= 400 or emails.status_code >= 400:
                raise SsoError("GitHub did not share your profile or e-mail addresses")
            profile, rows = user.json(), emails.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SsoError(f"Could not reach GitHub ({type(e).__name__})") from None
    if not isinstance(profile, dict) or not profile.get("id") or not isinstance(rows, list):
        raise SsoError("GitHub returned an unexpected answer")
    verified = [r for r in rows if isinstance(r, dict) and r.get("verified") is True and r.get("email")]
    verified.sort(key=lambda r: not r.get("primary"))
    return profile, [str(r["email"]).strip().lower() for r in verified]


def _config_for(db: Session, emails: list[str]) -> tuple[GithubLoginConfig, str] | None:
    for email in emails:
        domain = email.rsplit("@", 1)[-1]
        for cfg in db.scalars(select(GithubLoginConfig).where(GithubLoginConfig.enabled.is_(True))):
            if domain in [d.lower() for d in cfg.allowed_domains]:
                return cfg, email
    return None


def complete(db: Session, state: str, code: str, redirect_uri: str) -> User:
    """Validate the callback, read who GitHub says this is and return the (possibly new) user."""
    flow = db.get(SsoFlow, state)
    expired = flow is None or _aware(flow.created_at) < datetime.now(UTC) - FLOW_TTL
    if flow is None or expired or flow.org_id != FLOW_ORG or flow.login_code:
        raise SsoError("This sign-in link expired or was already used. Start again.")
    if not app_configured():
        raise SsoError("Signing in with GitHub is not set up")
    flow.login_code = "used"  # a flow can be completed once, even if validation below fails
    db.commit()
    profile, emails = _profile(code, redirect_uri, flow.code_verifier)
    if not emails:
        raise SsoError("Your GitHub account has no verified e-mail address")
    match = _config_for(db, emails)
    if match is None:
        raise SsoError("None of your verified GitHub e-mail addresses is in a domain allowed to sign in")
    cfg, email = match
    user = _user_for(db, cfg, email, profile)
    flow.login_code, flow.user_id = "", user.id  # cleared below once the SPA exchange code is minted
    return user


def _user_for(db: Session, cfg: GithubLoginConfig, email: str, profile: dict) -> User:
    external = f"github:{profile['id']}"
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        if user.org_id != cfg.org_id:
            raise SsoError("This e-mail address belongs to another organisation")
        if not user.active:
            raise SsoError("This account is deactivated")
        if user.external_id and user.external_id != external and user.auth_source != "password":
            raise SsoError("This account is linked to a different identity")
        user.external_id = user.external_id or external
        db.commit()
        return user
    if not cfg.auto_provision:
        raise SsoError("Your account has not been created yet. Ask an administrator.")
    role = cfg.default_role if cfg.default_role in SSO_ROLES else "viewer"
    user = User(
        org_id=cfg.org_id,
        email=email,
        name=str(profile.get("name") or profile.get("login") or "")[:200],
        role=role,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        auth_source="github",
        external_id=external,
    )
    db.add(user)
    db.commit()
    return user
