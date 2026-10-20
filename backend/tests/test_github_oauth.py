import json

import httpx
import pytest

from repolens.api import github as gh_api
from repolens.api import settings as settings_api
from repolens.models import AuditEvent, GithubConnection, OAuthFlow
from repolens.services import llm
from repolens.services.llm import LLMResult
from tests.conftest import register

OAUTH_TOKEN = "gho_" + "b" * 36
DEVICE_CODE = "dev-code-secret-0123456789"


@pytest.fixture
def gh(monkeypatch):
    state = {"polls": [], "requests": [], "start_error": None}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        host, path = request.url.host, request.url.path
        if host == "github.com" and path == "/login/device/code":
            if state["start_error"]:
                return httpx.Response(
                    200, json={"error": "device_flow_disabled", "error_description": "Device Flow must be enabled"}
                )
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            state["start_body"] = body
            return httpx.Response(
                200,
                json={
                    "device_code": DEVICE_CODE,
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                },
            )
        if host == "github.com" and path == "/login/oauth/access_token":
            state["poll_body"] = request.content.decode()
            step = state["polls"].pop(0) if state["polls"] else {"error": "authorization_pending"}
            return httpx.Response(200, json=step)
        if host == "api.github.com" and path == "/user":
            return httpx.Response(200, json={"login": "octo-admin"})
        if host == "models.github.ai" and path == "/inference/chat/completions":
            state["inference"] = request
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "OK"}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 1},
                },
            )
        if host == "models.github.ai" and path == "/catalog/models":
            return httpx.Response(
                200, json=[{"id": "openai/gpt-4.1", "name": "GPT-4.1", "publisher": "OpenAI"}, {"name": "no-id"}]
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(gh_api, "_transport", transport)
    monkeypatch.setattr(settings_api, "_catalog_transport", transport)
    monkeypatch.setattr(llm, "_openai_transport", transport)
    return state


def start(client, auth, **body):
    return client.post("/api/github/oauth/device/start", headers=auth, json={"client_id": "Iv1.abc123", **body})


def test_device_flow_happy_path_stores_encrypted_token(client, auth, gh, session):
    gh["polls"] = [
        {"error": "authorization_pending"},
        {"access_token": OAUTH_TOKEN, "token_type": "bearer", "scope": "repo,read:user"},
    ]
    s = start(client, auth).json()
    assert (
        s["user_code"] == "ABCD-1234"
        and s["verification_uri"].startswith("https://github.com/")
        and "device_code" not in s
    )
    assert gh["start_body"]["client_id"] == "Iv1.abc123" and "repo" in gh["start_body"]["scope"]
    poll = lambda: client.post("/api/github/oauth/device/poll", headers=auth, json={"flow_id": s["flow_id"]}).json()  # noqa: E731
    assert poll()["status"] == "pending"
    done = poll()
    assert done["status"] == "success" and done["login"] == "octo-admin" and done["auth_method"] == "oauth"
    assert (
        OAUTH_TOKEN not in json.dumps(done) and DEVICE_CODE not in json.dumps(done) and DEVICE_CODE not in json.dumps(s)
    )
    row = session.query(GithubConnection).one()
    assert row.auth_method == "oauth" and OAUTH_TOKEN not in row.token_encrypted and row.client_id == "Iv1.abc123"
    assert session.query(OAuthFlow).count() == 0  # flow consumed
    assert "device_code=" + DEVICE_CODE in gh["poll_body"]
    assert session.query(AuditEvent).filter_by(action="github.oauth.connected").count() == 1
    conn = client.get("/api/github/connection", headers=auth).json()
    assert conn["connected"] and conn["login"] == "octo-admin" and OAUTH_TOKEN not in json.dumps(conn)


def test_connection_token_is_used_for_pr_sync_calls(client, auth, gh):
    gh["polls"] = [{"access_token": OAUTH_TOKEN, "scope": "repo"}]
    s = start(client, auth).json()
    client.post("/api/github/oauth/device/poll", headers=auth, json={"flow_id": s["flow_id"]})
    assert client.post("/api/github/test", headers=auth).json() == {"ok": True, "user": "octo-admin"}
    assert gh["requests"][-1].headers["authorization"] == f"Bearer {OAUTH_TOKEN}"


def test_slow_down_denied_expired_and_errors(client, auth, gh, session):
    s = start(client, auth).json()
    poll = lambda: client.post("/api/github/oauth/device/poll", headers=auth, json={"flow_id": s["flow_id"]}).json()  # noqa: E731
    gh["polls"] = [{"error": "slow_down", "interval": 10}]
    assert poll() == {"status": "pending", "interval": 10}
    gh["polls"] = [{"error": "access_denied"}]
    assert poll()["status"] == "denied"
    assert (
        client.post("/api/github/oauth/device/poll", headers=auth, json={"flow_id": s["flow_id"]}).status_code == 404
    )  # consumed
    s2 = start(client, auth).json()
    gh["polls"] = [{"error": "expired_token"}]
    assert (
        client.post("/api/github/oauth/device/poll", headers=auth, json={"flow_id": s2["flow_id"]}).json()["status"]
        == "expired"
    )
    assert session.query(GithubConnection).count() == 0


def test_flow_expiry_is_enforced_locally(client, auth, gh, session):
    from datetime import UTC, datetime, timedelta

    s = start(client, auth).json()
    session.query(OAuthFlow).update({"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    session.commit()
    assert client.post("/api/github/oauth/device/poll", headers=auth, json={"flow_id": s["flow_id"]}).json() == {
        "status": "expired"
    }


def test_start_requires_client_id_and_surfaces_github_errors(client, auth, gh):
    assert client.post("/api/github/oauth/device/start", headers=auth, json={}).status_code == 400
    gh["start_error"] = True
    r = start(client, auth)
    assert r.status_code == 502 and "Device Flow" in r.text


def test_client_id_can_come_from_server_config(client, auth, gh, monkeypatch):
    from repolens.config import get_settings

    monkeypatch.setenv("REPOLENS_GITHUB_CLIENT_ID", "Iv1.server")
    get_settings.cache_clear()
    assert client.post("/api/github/oauth/device/start", headers=auth, json={}).status_code == 200
    assert gh["start_body"]["client_id"] == "Iv1.server"
    assert client.get("/api/github/connection", headers=auth).json()["oauth_client_id"] == "Iv1.server"


def test_flow_is_bound_to_the_admin_who_started_it_and_org(client, auth, gh):
    s = start(client, auth).json()
    other_org = register(client, "x@other.io", "Other")
    assert (
        client.post("/api/github/oauth/device/poll", headers=other_org, json={"flow_id": s["flow_id"]}).status_code
        == 404
    )
    client.post(
        "/api/auth/users", headers=auth, json={"email": "a2@acme.io", "password": "admin-pass-12", "role": "admin"}
    )
    tok = client.post("/api/auth/login", json={"email": "a2@acme.io", "password": "admin-pass-12"}).json()[
        "access_token"
    ]
    assert (
        client.post(
            "/api/github/oauth/device/poll", headers={"Authorization": f"Bearer {tok}"}, json={"flow_id": s["flow_id"]}
        ).status_code
        == 404
    )


def test_only_admins_can_start_the_flow(client, auth, gh):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    tok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
        "access_token"
    ]
    r = client.post(
        "/api/github/oauth/device/start", headers={"Authorization": f"Bearer {tok}"}, json={"client_id": "x"}
    )
    assert r.status_code == 403 and not gh["requests"]


def test_ghe_uses_its_own_oauth_host(client, auth, gh):
    from repolens.services.github import oauth_base

    assert oauth_base("https://api.github.com") == "https://github.com"
    assert oauth_base("https://ghe.acme.io/api/v3") == "https://ghe.acme.io"


# ------------------------------------------------------------------ GitHub Models as an LLM provider
def connect_oauth(client, auth, gh):
    gh["polls"] = [{"access_token": OAUTH_TOKEN, "scope": "repo"}]
    s = start(client, auth).json()
    client.post("/api/github/oauth/device/poll", headers=auth, json={"flow_id": s["flow_id"]})


def test_github_models_requires_a_github_connection_or_key(client, auth):
    r = client.put("/api/settings/llm", headers=auth, json={"provider": "github-models", "model": "openai/gpt-4.1"})
    assert r.status_code == 400 and "Connect GitHub" in r.text


def test_github_models_uses_connected_token_and_endpoint(client, auth, gh):
    connect_oauth(client, auth, gh)
    r = client.put("/api/settings/llm", headers=auth, json={"provider": "github-models", "model": "openai/gpt-4.1"})
    assert r.status_code == 200
    v = r.json()
    assert (
        v["provider"] == "github-models"
        and v["offline"] is False
        and v["uses_github_token"] is True
        and v["has_key"] is True
    )
    assert OAUTH_TOKEN not in json.dumps(v)
    t = client.post("/api/settings/llm/test", headers=auth).json()
    assert t["ok"] is True and t["input_tokens"] == 9
    req = gh["inference"]
    assert str(req.url) == "https://models.github.ai/inference/chat/completions"
    assert req.headers["authorization"] == f"Bearer {OAUTH_TOKEN}"
    assert json.loads(req.content)["model"] == "openai/gpt-4.1"


def test_github_models_own_token_overrides_connection(client, auth, gh):
    connect_oauth(client, auth, gh)
    client.put(
        "/api/settings/llm",
        headers=auth,
        json={"provider": "github-models", "model": "openai/gpt-4.1", "api_key": "github_pat_own_token_123"},
    )
    v = client.get("/api/settings/llm", headers=auth).json()
    assert v["uses_github_token"] is False
    client.post("/api/settings/llm/test", headers=auth)
    assert gh["inference"].headers["authorization"] == "Bearer github_pat_own_token_123"


def test_github_models_catalog(client, auth, gh):
    assert client.get("/api/settings/llm/github-models/catalog", headers=auth).status_code == 400  # not connected
    connect_oauth(client, auth, gh)
    cat = client.get("/api/settings/llm/github-models/catalog", headers=auth).json()
    assert cat == [{"id": "openai/gpt-4.1", "name": "GPT-4.1", "publisher": "OpenAI"}]


def test_github_models_powers_ask_with_usage_recorded(client, auth, gh, monkeypatch, demo_repo):
    from tests.test_api import add_repo

    connect_oauth(client, auth, gh)
    client.put(
        "/api/settings/llm",
        headers=auth,
        json={"provider": "github-models", "model": "openai/gpt-4.1", "input_price": 2.0, "output_price": 8.0},
    )
    seen = {}

    def fake(system, prompt, max_tokens, cfg=None):
        seen["cfg"] = cfg
        return LLMResult("Deleted users get a default profile [S1].", 1000, 50)

    monkeypatch.setattr(llm, "_openai_call", fake)
    add_repo(client, auth, demo_repo[0])
    a = client.post("/api/knowledge/ask", headers=auth, json={"query": "How are deleted users handled?"}).json()
    assert (
        a["mode"] == "llm"
        and seen["cfg"].provider == "github-models"
        and seen["cfg"].base_url == "https://models.github.ai/inference"
    )
    ev = [e for e in client.get("/api/usage/events", headers=auth).json() if e["kind"] == "chat"][0]
    assert ev["provider"] == "github-models" and ev["estimated_cost"] == pytest.approx(0.0024)
