import importlib
import inspect

import pytest
from fastapi.routing import APIRoute

from repolens import config
from repolens.services import passwords, ratelimit
from tests.conftest import register


def limits(monkeypatch, **env):
    monkeypatch.setenv("REPOLENS_RATE_LIMIT_ENABLED", "true")
    for k, v in env.items():
        monkeypatch.setenv(f"REPOLENS_{k.upper()}", str(v))
    config.get_settings.cache_clear()
    ratelimit.reset()


# ------------------------------------------------------------------------------------------------ headers and size
def test_api_answers_carry_security_headers_and_are_never_cached(client, auth):
    r = client.get("/api/auth/me", headers=auth)
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer" and "permissions-policy" in r.headers


def test_oversized_bodies_are_refused_before_they_are_read(client, auth, monkeypatch):
    monkeypatch.setenv("REPOLENS_MAX_BODY_MB", "1")
    config.get_settings.cache_clear()
    big = "x" * (2 * 1024 * 1024)
    r = client.post("/api/knowledge/collections", headers=auth, json={"name": big})
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    assert client.post("/api/knowledge/collections", headers=auth, json={"name": "fine"}).status_code == 201


# ------------------------------------------------------------------------------------------------ rate limits
def test_sign_in_and_registration_are_limited_per_address(client, monkeypatch):
    limits(monkeypatch, rate_limit_auth_per_minute=3)
    body = {"email": "nobody@example.test", "password": "whatever-123"}
    codes = [client.post("/api/auth/login", json=body).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    r = client.post("/api/auth/register", json={"email": "a@example.test", "password": "x" * 12, "organization": "A"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "Too many requests" in r.json()["detail"]
    assert client.get("/healthz").status_code == 200  # probes are never limited


def test_a_caller_cannot_use_all_the_capacity(client, auth, monkeypatch):
    limits(monkeypatch, rate_limit_user_per_minute=5, rate_limit_ip_per_minute=1000)
    codes = [client.get("/api/auth/me", headers=auth).status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and set(codes[5:]) == {429}
    other = register(client, "other@example.test", "Other")  # a different session is unaffected
    assert client.get("/api/auth/me", headers=other).status_code == 200


def test_model_backed_calls_have_a_tighter_limit(client, auth, monkeypatch):
    limits(monkeypatch, rate_limit_heavy_per_minute=2)
    codes = [
        client.post("/api/knowledge/ask", headers=auth, json={"query": "what is this"}).status_code for _ in range(4)
    ]
    assert codes[:2] == [200, 200] and codes[2:] == [429, 429]
    assert client.get("/api/repositories", headers=auth).status_code == 200  # ordinary reads are not affected


def test_a_forged_forwarded_for_header_does_not_dodge_the_limit(client, monkeypatch):
    limits(monkeypatch, rate_limit_ip_per_minute=3, trusted_proxy_hops=1)
    body = {"email": "x@example.test", "password": "whatever-123"}
    # the trusted proxy appends the real address (9.9.9.9); everything to its left is client-supplied
    codes = [
        client.post("/api/auth/login", json=body, headers={"X-Forwarded-For": f"10.0.0.{i}, 9.9.9.9"}).status_code
        for i in range(5)
    ]
    assert codes[:3] == [401] * 3 and codes[3:] == [429, 429]
    other = client.post("/api/auth/login", json=body, headers={"X-Forwarded-For": "10.0.0.1, 8.8.8.8"})
    assert other.status_code == 401  # a genuinely different client is not blocked


def test_without_trusted_proxies_the_header_is_ignored(client, monkeypatch):
    limits(monkeypatch, rate_limit_ip_per_minute=2, trusted_proxy_hops=0)
    body = {"email": "x@example.test", "password": "whatever-123"}
    codes = [
        client.post("/api/auth/login", json=body, headers={"X-Forwarded-For": f"1.1.1.{i}"}).status_code
        for i in range(4)
    ]
    assert codes == [401, 401, 429, 429]


# ------------------------------------------------------------------------------------------------ passwords and secrets
@pytest.mark.parametrize(
    "pw,why",
    [
        ("short1", "at least 8"),
        ("Password123!", "most common"),
        ("12345678", "most common"),
        ("aaaaaaaaaaaa", "different characters"),
        ("jane.doe2026", "e-mail name"),
        ("x" * 80, "72 bytes"),
    ],
)
def test_weak_passwords_are_refused_everywhere_they_are_set(client, auth, pw, why):
    r = client.post("/api/auth/register", json={"email": "jane.doe@example.test", "password": pw, "organization": "J"})
    assert r.status_code in (400, 422) and (why in r.text or r.status_code == 422)
    r = client.post(
        "/api/auth/users", headers=auth, json={"email": "jane.doe@acme.io", "password": pw, "role": "member"}
    )
    assert r.status_code in (400, 422)


def test_a_good_passphrase_is_accepted_and_policy_is_deterministic():
    assert passwords.check_password("maple orbit lantern 2026", "a@b.io") is None
    assert passwords.check_password("Password123!") and passwords.check_password("correct-horse-1") is None


def test_production_refuses_a_short_or_default_signing_secret(monkeypatch):
    from repolens.main import lifespan

    monkeypatch.setenv("REPOLENS_ENV", "prod")
    monkeypatch.setenv("REPOLENS_JWT_SECRET", "too-short-secret")
    config.get_settings.cache_clear()
    import asyncio

    async def boot():
        async with lifespan(None):  # type: ignore[arg-type]
            pass

    with pytest.raises(RuntimeError, match="at least 32"):
        asyncio.run(boot())


def test_metrics_can_require_a_bearer_token(client, monkeypatch):
    assert client.get("/metrics").status_code == 200
    monkeypatch.setenv("REPOLENS_METRICS_TOKEN", "scrape-token-123")
    config.get_settings.cache_clear()
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer scrape-token-123"}).status_code == 200


# ------------------------------------------------------------------------------------------------ authorization audit
PUBLIC = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/reset"),  # the one-time link is the credential
    ("GET", "/api/auth/sso/discover"),
    ("GET", "/api/auth/sso/start"),
    ("GET", "/api/auth/sso/callback"),
    ("POST", "/api/auth/sso/exchange"),
    ("POST", "/api/webhooks/github"),
}
OWN_ACCOUNT_OR_READ = {  # POSTs that read, or act only on the caller's own account
    "/api/auth/logout",
    "/api/auth/logout-others",
    "/api/auth/password",
    "/api/auth/sessions/{session_id}",
    "/api/auth/me",
    "/api/auth/totp/setup",  # the authenticator app: each acts only on the caller's own account
    "/api/auth/totp/enable",
    "/api/auth/totp/disable",
    "/api/investigations/ci/parse",
    "/api/graph/heat",
    "/api/knowledge/ask",
    "/api/knowledge/search",
    "/api/knowledge/similar",
    "/api/mcp",  # every tool runs its own policy check and audit entry
    "/api/auth/api-tokens/{token_id}",  # a person revokes only their own
    "/api/wiki/ask",
    "/api/wiki/similar",
}


def test_every_operation_requires_a_session_unless_it_is_deliberately_public(client):
    ops = [
        (m.upper(), p, o)
        for p, v in client.app.openapi()["paths"].items()
        for m, o in v.items()
        if m in ("get", "post", "put", "patch", "delete")
    ]
    open_ = {(m, p) for m, p, o in ops if not o.get("security")}
    scim = {(m, p) for m, p in open_ if p.startswith("/scim/")}  # SCIM has its own hashed bearer token
    assert open_ - scim == PUBLIC
    for m, p in sorted(scim):
        r = client.request(m, p.replace("{uid}", "x"), json={} if m in ("POST", "PUT", "PATCH") else None)
        assert r.status_code == 401, (m, p)
    for m, p, o in ops:
        if o.get("security"):
            r = client.request(
                m, p.replace("{", "").replace("}", ""), json={} if m in ("POST", "PUT", "PATCH") else None
            )
            assert r.status_code in (401, 403), (m, p, r.status_code)


def test_every_mutating_operation_runs_a_policy_check(client):
    import pkgutil

    import repolens.api as api_package

    mods = [m.name for m in pkgutil.iter_modules(api_package.__path__)]  # every API module, including new ones
    unguarded = []
    for m in mods:
        mod = importlib.import_module("repolens.api." + m)
        for name in ("router", "public", "admin"):
            r = getattr(mod, name, None)
            for x in getattr(r, "routes", []):
                if not isinstance(x, APIRoute):
                    continue
                for meth in x.methods - {"GET", "HEAD", "OPTIONS"}:
                    src = inspect.getsource(x.endpoint)
                    if "authorize(" not in src and "require_role" not in str(x.dependant.dependencies):
                        unguarded.append((meth, x.path))
    assert {p for _, p in unguarded} <= OWN_ACCOUNT_OR_READ | {
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/reset",  # the one-time link is the credential
        "/api/auth/sso/exchange",
        "/api/webhooks/github",
        "/scim/v2/Users",
        "/scim/v2/Users/{uid}",
    }, unguarded


def test_a_viewer_cannot_change_anything(client, auth):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"}
    )
    v = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()[
            "access_token"
        ]
    }
    attempts = [
        ("POST", "/api/repositories", {"name": "x", "source": "local", "url": "/tmp"}),
        ("POST", "/api/knowledge/collections", {"name": "docs"}),
        ("POST", "/api/investigations", {"query": "a bug"}),
        ("PUT", "/api/settings/routing", {"savings_mode": False}),
        ("POST", "/api/auth/users", {"email": "z@acme.io", "password": "viewer-pass-1", "role": "admin"}),
        ("PUT", "/api/wiki/page", {"repo_id": "x", "path": "a.md", "content": "x"}),
        ("DELETE", "/api/settings/memory", None),
        (
            "PUT",
            "/api/jira/connection",
            {"base_url": "https://a.atlassian.net", "deployment": "cloud", "email": "a@b.co", "token": "tok-123456"},
        ),
    ]
    for m, p, b in attempts:
        r = client.request(m, p, headers=v, json=b)
        assert r.status_code in (403, 404), (m, p, r.status_code, r.text[:100])


def test_failed_sign_ins_leave_an_audit_trail_without_the_password(client, auth, session):
    from repolens.models import AuditEvent

    client.post(
        "/api/auth/users", headers=auth, json={"email": "t@acme.io", "password": "Maple-Orbit-2026", "role": "member"}
    )
    client.post("/api/auth/login", json={"email": "t@acme.io", "password": "guess-number-1"})
    client.post("/api/auth/login", json={"email": "ghost@acme.io", "password": "guess-number-2"})
    rows = session.query(AuditEvent).filter_by(action="auth.login_failed").all()
    assert (
        len(rows) == 1 and rows[0].target == "t@acme.io" and rows[0].allowed is False
    )  # unknown accounts leave nothing to hang it on
    assert "guess-number" not in str(rows[0].detail) and "ip" in rows[0].detail


def test_interactive_docs_exist_only_in_development(monkeypatch):
    from fastapi.testclient import TestClient

    from repolens.main import create_app

    assert TestClient(create_app()).get("/docs").status_code == 200
    monkeypatch.setenv("REPOLENS_ENV", "prod")
    config.get_settings.cache_clear()
    c = TestClient(create_app())
    assert (
        c.get("/docs").status_code == 404
        and c.get("/openapi.json").status_code == 404
        and c.get("/redoc").status_code == 404
    )


def test_system_metrics_report_cpu_memory_network_and_request_rate(client, auth, monkeypatch):
    from repolens.services import sysmetrics

    monkeypatch.setenv("REPOLENS_RATE_LIMIT_ENABLED", "false")
    for _ in range(3):
        client.get("/api/auth/me", headers=auth)
    sysmetrics.sample()
    s = client.get("/api/metrics/system", headers=auth).json()
    assert s["cores"] >= 1 and s["interval_s"] == 5.0 and s["history"] and "load_average" in s
    cur = s["current"]
    assert {
        "host_cpu",
        "process_cpu",
        "mem_used",
        "mem_total",
        "process_rss",
        "rx_bps",
        "tx_bps",
        "rps",
        "p95_ms",
    } <= set(cur)
    if s["available"]:  # Linux
        assert cur["mem_total"] > 0 and cur["process_rss"] > 0
    # only admins may read how loaded the server is
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    m = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
            "access_token"
        ]
    }
    assert client.get("/api/metrics/system", headers=m).status_code == 403
