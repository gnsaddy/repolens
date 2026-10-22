import json

import pytest

from repolens.config import get_settings
from repolens.models import AuditEvent, LLMSetting
from repolens.services import llm
from repolens.services.llm import LLMResult
from tests.conftest import register
from tests.test_api import add_repo

KEY = "sk-ant-test-0123456789abcdefghijklmnop"


def put(client, auth, **kw):
    body = {"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": KEY, **kw}
    return client.put("/api/settings/llm", headers=auth, json=body)


def test_defaults_to_env_offline_with_presets(client, auth):
    v = client.get("/api/settings/llm", headers=auth).json()
    assert v["source"] == "env" and v["offline"] is True and v["has_key"] is False
    assert "claude-sonnet-5-5" in v["presets"]["anthropic"]


def test_key_is_encrypted_and_never_returned(client, auth, session):
    r = put(client, auth)
    assert r.status_code == 200
    v = r.json()
    assert v["has_key"] is True and v["source"] == "org" and v["offline"] is False
    assert KEY not in json.dumps(v) and KEY not in json.dumps(client.get("/api/settings/llm", headers=auth).json())
    row = session.query(LLMSetting).one()
    assert KEY not in row.api_key_encrypted and row.api_key_encrypted.startswith("gAAAA")


def test_key_required_and_kept_when_blank(client, auth):
    assert (
        client.put("/api/settings/llm", headers=auth, json={"provider": "openai", "model": "gpt-x"}).status_code == 400
    )
    put(client, auth)
    r = client.put("/api/settings/llm", headers=auth, json={"provider": "anthropic", "model": "claude-haiku-5-5"})
    assert r.status_code == 200 and r.json()["has_key"] is True and r.json()["model"] == "claude-haiku-5-5"


def test_validation(client, auth):
    assert put(client, auth, provider="evil").status_code == 422
    assert put(client, auth, model="bad model; rm -rf").status_code == 422
    assert put(client, auth, provider="openai", base_url="ftp://x").status_code == 400
    assert put(client, auth, input_price=-1).status_code == 422


def test_only_admin_can_configure_and_denials_are_audited(client, auth, session):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    tok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
        "access_token"
    ]
    member = {"Authorization": f"Bearer {tok}"}
    assert put(client, member).status_code == 403
    assert client.post("/api/settings/llm/test", headers=member).status_code == 403
    assert client.delete("/api/settings/llm", headers=member).status_code == 403
    assert (
        client.get("/api/settings/llm", headers=member).status_code == 200
    )  # members may read the (secret-free) status
    assert session.query(AuditEvent).filter_by(action="llm.configure", allowed=False).count() == 2


def test_settings_are_per_organisation(client, auth):
    put(client, auth)
    other = register(client, "o@other.io", "Other")
    v = client.get("/api/settings/llm", headers=other).json()
    assert v["source"] == "env" and v["has_key"] is False


def test_configured_key_is_used_for_calls_with_price_override(client, auth, demo_repo, monkeypatch):
    seen = {}

    def fake(system, prompt, max_tokens, cfg=None):
        seen["key"], seen["model"] = cfg.api_key, cfg.model
        return LLMResult(json.dumps({"root_cause": "x", "explanation": "y", "suggested_fix": "z"}), 1_000_000, 100_000)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    put(client, auth, input_price=3.0, output_price=15.0)
    repo = add_repo(client, auth, demo_repo[0])
    from repolens.demo import BUG_REPORT

    r = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [repo["id"]]})
    inv = client.get(f"/api/investigations/{r.json()['id']}", headers=auth).json()
    assert inv["status"] == "completed" and seen == {"key": KEY, "model": "claude-sonnet-5-5"}
    ev = [e for e in client.get("/api/usage/events", headers=auth).json() if e["kind"] == "chat"][0]
    assert ev["estimated_cost"] == pytest.approx(3.0 + 1.5) and ev["pricing_version"] == "org-settings"
    assert "$4.5000" in inv["report_md"]


def test_connection_test_success_and_failure_redacts_key(client, auth, monkeypatch):
    put(client, auth)
    monkeypatch.setattr(llm, "_anthropic_call", lambda s, p, m, cfg=None: LLMResult("OK", 12, 1))
    ok = client.post("/api/settings/llm/test", headers=auth).json()
    assert ok["ok"] is True and ok["input_tokens"] == 12

    def boom(s, p, m, cfg=None):
        raise RuntimeError(f"401 Unauthorized for key {KEY}")

    monkeypatch.setattr(llm, "_anthropic_call", boom)
    bad = client.post("/api/settings/llm/test", headers=auth).json()
    assert bad["ok"] is False and KEY not in json.dumps(bad) and "REDACTED" in bad["error"]
    events = client.get("/api/usage/events", headers=auth).json()
    assert any(not e["success"] and KEY not in (e.get("error") or "") for e in events)


def test_test_endpoint_reports_offline(client, auth):
    r = client.post("/api/settings/llm/test", headers=auth).json()
    assert r["ok"] is False and "offline" in r["error"]


def test_reset_reverts_to_env_and_forgets_key(client, auth, session):
    put(client, auth)
    v = client.delete("/api/settings/llm", headers=auth).json()
    assert v["source"] == "env" and v["has_key"] is False and session.query(LLMSetting).count() == 0


def test_env_provider_still_works_without_org_setting(client, auth, monkeypatch):
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("REPOLENS_LLM_API_KEY", "env-key-123456")
    get_settings.cache_clear()
    v = client.get("/api/settings/llm", headers=auth).json()
    assert v["source"] == "env" and v["has_key"] is True and v["offline"] is False


def test_non_json_model_replies_give_a_clear_error_not_a_decode_error(monkeypatch):
    import httpx
    import pytest

    from repolens.services import llm

    cfg = llm.LLMConfig("github-models", "openai/gpt-4.1", "https://models.github.ai/inference", "tok", "org")
    monkeypatch.setattr(
        llm,
        "_openai_transport",
        httpx.MockTransport(lambda r: httpx.Response(200, text="OK\r\n", headers={"content-type": "text/plain"})),
    )
    with pytest.raises(
        RuntimeError, match=r"models.github.ai answered 200 with something that is not a model reply.*'OK'"
    ):
        llm._openai_call("sys", "hi", 10, cfg)
    monkeypatch.setattr(llm, "_openai_transport", httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": 1})))
    with pytest.raises(RuntimeError, match="unexpected reply"):
        llm._openai_call("sys", "hi", 10, cfg)
    monkeypatch.setattr(
        llm, "_openai_transport", httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "bad"}))
    )
    with pytest.raises(RuntimeError, match="rejected the credentials"):
        llm._openai_call("sys", "hi", 10, cfg)
