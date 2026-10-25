import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from repolens.models import SsoFlow, User
from repolens.services import sso
from tests.conftest import register

ISSUER = "https://idp.example.com"
CLIENT_ID = "repolens-client"


class FakeIdP:
    """Just enough of an OpenID Connect provider: discovery, JWKS and a token endpoint that enforces PKCE."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.claims: dict = {}
        self.pending: dict[str, dict] = {}
        self.sign_with = self.key
        self.alg = "RS256"
        self.requests: list[str] = []

    def jwks(self):
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def transport(self):
        def handler(r: httpx.Request) -> httpx.Response:
            self.requests.append(str(r.url))
            p = r.url.path
            if p == "/.well-known/openid-configuration":
                return httpx.Response(
                    200,
                    json={
                        "issuer": ISSUER,
                        "authorization_endpoint": f"{ISSUER}/authorize",
                        "token_endpoint": f"{ISSUER}/token",
                        "jwks_uri": f"{ISSUER}/jwks",
                    },
                )
            if p == "/jwks":
                return httpx.Response(200, json=self.jwks())
            if p == "/token":
                form = {k: v[0] for k, v in parse_qs(r.content.decode()).items()}
                auth = self.pending.get(form["code"])
                if auth is None or form["client_secret"] != "s3cret" or form["client_id"] != CLIENT_ID:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
                    .rstrip(b"=")
                    .decode()
                )
                if challenge != auth["code_challenge"]:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
                now = int(time.time())
                claims = {
                    "iss": ISSUER,
                    "aud": CLIENT_ID,
                    "sub": "idp-user-1",
                    "email": "ana@corp.io",
                    "email_verified": True,
                    "name": "Ana",
                    "iat": now,
                    "exp": now + 300,
                    "nonce": auth["nonce"],
                    **self.claims,
                }
                claims = {k: v for k, v in claims.items() if v is not None}
                return httpx.Response(
                    200,
                    json={
                        "id_token": jwt.encode(claims, self.sign_with, algorithm=self.alg, headers={"kid": self.kid}),
                        "access_token": "ignored",
                    },
                )
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def authorize(self, location: str) -> str:
        """The browser visits the authorize URL; the IdP remembers PKCE+nonce and returns a code."""
        q = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
        assert q["code_challenge_method"] == "S256" and q["client_id"] == CLIENT_ID and q["response_type"] == "code"
        assert "openid" in q["scope"] and q["redirect_uri"].endswith("/api/auth/sso/callback")
        code = "code-" + q["state"][:10]
        self.pending[code] = {"code_challenge": q["code_challenge"], "nonce": q["nonce"]}
        return code, q["state"]


@pytest.fixture
def idp(monkeypatch):
    f = FakeIdP()
    monkeypatch.setattr(sso, "transport", f.transport())
    sso._discovery.clear()
    return f


def configure(client, auth, **over):
    body = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret": "s3cret",
        "allowed_domains": "corp.io",
        "default_role": "viewer",
        **over,
    }
    return client.put("/api/settings/sso", headers=auth, json=body)


def login(client, idp, email="ana@corp.io"):
    r = client.get("/api/auth/sso/start", params={"email": email}, follow_redirects=False)
    assert r.status_code == 302, r.text
    code, state = idp.authorize(r.headers["location"])
    cb = client.get("/api/auth/sso/callback", params={"code": code, "state": state}, follow_redirects=False)
    return cb, state


def complete(client, cb):
    assert cb.headers["location"].startswith("/sso-complete?code="), cb.headers["location"]
    return client.post("/api/auth/sso/exchange", json={"code": cb.headers["location"].split("code=")[1]})


# ------------------------------------------------------------------------------------------------ OIDC
def test_full_sign_in_provisions_a_viewer_and_never_exposes_the_secret(client, auth, idp, session):
    r = configure(client, auth)
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["allowed_domains"] == ["corp.io"] and view["has_secret"] is True and "s3cret" not in json.dumps(view)
    assert view["redirect_uri"].endswith("/api/auth/sso/callback")
    assert client.get("/api/auth/sso/discover", params={"email": "ana@corp.io"}).json() == {
        "sso": True,
        "organization": "Acme",
    }
    assert client.get("/api/auth/sso/discover", params={"email": "bob@other.com"}).json() == {"sso": False}

    cb, state = login(client, idp)
    tok = complete(client, cb)
    assert tok.status_code == 200, tok.text
    assert tok.json()["user"]["email"] == "ana@corp.io" and tok.json()["user"]["role"] == "viewer"
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + tok.json()["access_token"]}).json()
    assert me["auth_source"] == "sso" and me["name"] == "Ana"
    session.expire_all()
    u = session.query(User).filter_by(email="ana@corp.io").one()
    assert u.external_id == "idp-user-1" and u.org_id == tok.json()["user"]["org_id"]
    # the state and the one-time code are single use
    assert (
        "/login?sso_error="
        in client.get("/api/auth/sso/callback", params={"code": "x", "state": state}, follow_redirects=False).headers[
            "location"
        ]
    )
    code = cb.headers["location"].split("code=")[1]
    assert client.post("/api/auth/sso/exchange", json={"code": code}).status_code == 401
    # a second sign-in finds the same account
    assert complete(client, login(client, idp)[0]).json()["user"]["id"] == u.id
    assert session.query(User).filter_by(email="ana@corp.io").count() == 1


def test_existing_member_keeps_their_role_and_admin_is_never_granted(client, auth, idp, session):
    configure(client, auth, default_role="member")
    client.post(
        "/api/auth/users", headers=auth, json={"email": "ana@corp.io", "password": "Maple-Orbit-2026", "role": "admin"}
    )
    out = complete(client, login(client, idp)[0]).json()
    assert out["user"]["role"] == "admin"  # linked to the existing account; its role is untouched
    assert (
        client.put(
            "/api/settings/sso",
            headers=auth,
            json={"issuer": ISSUER, "client_id": CLIENT_ID, "allowed_domains": "corp.io", "default_role": "admin"},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "claims, message",
    [
        ({"email_verified": False}, "not verified"),
        ({"email": "ana@evil.com"}, "domain is not allowed"),
        ({"aud": "someone-else"}, "could not be verified"),
        ({"iss": "https://evil.example"}, "could not be verified"),
        ({"exp": int(time.time()) - 3600}, "could not be verified"),
        ({"nonce": "forged"}, "does not belong"),
        ({"email": None}, "did not share an e-mail"),
    ],
)
def test_bad_id_tokens_are_rejected(client, auth, idp, claims, message):
    configure(client, auth)
    idp.claims = claims
    cb, _ = login(client, idp)
    assert "/login?sso_error=" in cb.headers["location"] and message.split()[0].lower() in cb.headers[
        "location"
    ].lower().replace("%20", " ")
    assert "sso-complete" not in cb.headers["location"]


def test_token_signed_by_an_unknown_key_or_with_none_algorithm_is_rejected(client, auth, idp):
    configure(client, auth)
    idp.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # not the key published in the JWKS
    cb, _ = login(client, idp)
    assert "sso_error" in cb.headers["location"]
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "email": "ana@corp.io", "exp": int(time.time()) + 99, "iat": 1},
        None,
        algorithm="none",
    )
    with pytest.raises(sso.SsoError):
        sso._signing_key(idp.jwks(), unsigned)


def test_users_of_other_organisations_deactivated_users_and_provisioning_off(client, auth, idp, session):
    configure(client, auth)
    other = register(client, "ana@corp.io", "Other")  # same e-mail already belongs to a different organisation
    assert other
    cb, _ = login(client, idp)
    assert "another%20organisation" in cb.headers["location"] or "another organisation" in cb.headers["location"]

    # a deactivated account of this org
    session.query(User).filter_by(email="ana@corp.io").delete()
    session.commit()
    configure(client, auth, auto_provision=False)
    cb, _ = login(client, idp)
    assert "not%20been%20created" in cb.headers["location"] or "not been created" in cb.headers["location"]
    configure(client, auth, auto_provision=True)
    assert complete(client, login(client, idp)[0]).status_code == 200
    u = session.query(User).filter_by(email="ana@corp.io").one()
    u.active = False
    session.commit()
    cb, _ = login(client, idp)
    assert "deactivated" in cb.headers["location"]


def test_expired_flows_and_pkce_binding(client, auth, idp, session):
    configure(client, auth)
    r = client.get("/api/auth/sso/start", params={"email": "ana@corp.io"}, follow_redirects=False)
    code, state = idp.authorize(r.headers["location"])
    flow = session.get(SsoFlow, state)
    from datetime import UTC, datetime, timedelta

    flow.created_at = datetime.now(UTC) - timedelta(minutes=11)
    session.commit()
    cb = client.get("/api/auth/sso/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert "expired" in cb.headers["location"]
    # a code issued for another sign-in's challenge fails the PKCE check at the provider
    r1 = client.get("/api/auth/sso/start", params={"email": "ana@corp.io"}, follow_redirects=False)
    c1, s1 = idp.authorize(r1.headers["location"])
    r2 = client.get("/api/auth/sso/start", params={"email": "ana@corp.io"}, follow_redirects=False)
    _, s2 = idp.authorize(r2.headers["location"])
    cb = client.get("/api/auth/sso/callback", params={"code": c1, "state": s2}, follow_redirects=False)
    assert "refused" in cb.headers["location"]


def test_admin_only_validation_and_domain_exclusivity(client, auth, idp):
    configure(client, auth)
    member = client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "Maple-Orbit-2026", "role": "member"}
    )
    assert member.status_code == 201
    mtok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "Maple-Orbit-2026"}).json()[
        "access_token"
    ]
    assert client.get("/api/settings/sso", headers={"Authorization": f"Bearer {mtok}"}).status_code == 403
    other = register(client, "o@other.io", "Other")
    clash = configure(client, other)
    assert clash.status_code == 409 and "corp.io" in clash.json()["detail"]
    for bad in (
        {"allowed_domains": "not a domain"},
        {"allowed_domains": ""},
        {"issuer": "http://idp.example.com"},
        {"issuer": "https://127.0.0.1"},
    ):
        assert configure(client, auth, **bad).status_code in (400, 422)
    assert client.delete("/api/settings/sso", headers=auth).status_code == 204
    assert client.get("/api/auth/sso/discover", params={"email": "ana@corp.io"}).json() == {"sso": False}
    assert client.get("/api/auth/sso/start", params={"email": "ana@corp.io"}, follow_redirects=False).status_code == 404


# ------------------------------------------------------------------------------------------------ SCIM
def scim_headers(client, auth):
    tok = client.post("/api/settings/scim/token", headers=auth).json()["token"]
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/scim+json"}, tok


def test_scim_lifecycle_and_deprovisioning_kills_sessions(client, auth, session):
    h, tok = scim_headers(client, auth)
    assert tok.startswith("scim_") and "token" not in client.get("/api/settings/scim", headers=auth).json()
    user = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "Bo@Corp.io",
        "externalId": "okta-77",
        "name": {"givenName": "Bo", "familyName": "Lee"},
        "emails": [{"value": "bo@corp.io", "primary": True}],
        "active": True,
    }
    r = client.post("/scim/v2/Users", headers=h, json=user)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert (
        r.json()["userName"] == "bo@corp.io"
        and r.json()["roles"] == [{"value": "viewer"}]
        and r.json()["name"]["familyName"] == "Lee"
    )
    assert client.post("/scim/v2/Users", headers=h, json=user).status_code == 409
    listed = client.get("/scim/v2/Users", headers=h, params={"filter": 'userName eq "BO@corp.io"'}).json()
    assert listed["totalResults"] == 1 and listed["Resources"][0]["id"] == uid
    assert (
        client.get("/scim/v2/Users", headers=h, params={"filter": 'externalId eq "okta-77"'}).json()["totalResults"]
        == 1
    )
    assert client.get("/scim/v2/Users", headers=h, params={"filter": "name sw B"}).status_code == 400
    # the provisioned user has no usable password but can have a role granted by the IdP
    patched = client.patch(
        f"/scim/v2/Users/{uid}",
        headers=h,
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "roles", "value": [{"value": "member"}]}],
        },
    )
    assert patched.json()["roles"] == [{"value": "member"}]
    # Entra ID style patch without a path
    assert (
        client.patch(
            f"/scim/v2/Users/{uid}",
            headers=h,
            json={"Operations": [{"op": "Replace", "value": {"displayName": "Bo L."}}]},
        ).json()["displayName"]
        == "Bo L."
    )
    # an existing session is invalidated the moment the user is deactivated
    from repolens.security import create_token

    session.expire_all()
    u = session.get(User, uid)
    session_token = create_token(u.id, u.org_id, u.role)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {session_token}"}).status_code == 200
    off = client.patch(
        f"/scim/v2/Users/{uid}", headers=h, json={"Operations": [{"op": "replace", "path": "active", "value": "False"}]}
    )
    assert off.json()["active"] is False
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {session_token}"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "bo@corp.io", "password": "whatever"}).status_code == 401
    assert client.put(f"/scim/v2/Users/{uid}", headers=h, json={**user, "active": True}).json()["active"] is True
    assert client.delete(f"/scim/v2/Users/{uid}", headers=h).status_code == 204
    session.expire_all()
    assert session.get(User, uid).active is False  # kept for history, just disabled


def test_scim_authentication_isolation_and_rotation(client, auth):
    h, tok = scim_headers(client, auth)
    assert client.get("/scim/v2/Users").status_code == 401
    assert client.get("/scim/v2/Users", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/scim/v2/ServiceProviderConfig", headers=h).json()["patch"]["supported"] is True
    assert client.get("/scim/v2/ResourceTypes", headers=h).status_code == 200
    created = client.post("/scim/v2/Users", headers=h, json={"userName": "x@corp.io"}).json()
    other = register(client, "o@other.io", "Other")
    oh, _ = scim_headers(client, other)
    assert (
        client.get(f"/scim/v2/Users/{created['id']}", headers=oh).status_code == 404
    )  # another tenant's token cannot see it
    assert [u["userName"] for u in client.get("/scim/v2/Users", headers=oh).json()["Resources"]] == [
        "o@other.io"
    ]  # only its own
    assert client.post("/scim/v2/Users", headers=h, json={"userName": "not-an-email"}).status_code == 400
    # rotating the token invalidates the old one
    new = client.post("/api/settings/scim/token", headers=auth).json()["token"]
    assert new != tok and client.get("/scim/v2/Users", headers=h).status_code == 401
    assert client.get("/scim/v2/Users", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    assert client.get("/api/settings/scim", headers=auth).json()["last_used_at"] is not None
    assert client.delete("/api/settings/scim", headers=auth).status_code == 204
    assert client.get("/scim/v2/Users", headers={"Authorization": f"Bearer {new}"}).status_code == 401
