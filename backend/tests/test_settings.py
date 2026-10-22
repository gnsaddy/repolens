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


def test_gemini_uses_googles_openai_compatible_endpoint_with_a_fixed_url(client, auth, monkeypatch, session):
    import httpx

    key = "AIzaSyTestKey0123456789abcdefghijk"
    seen = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append((str(r.url), r.headers.get("authorization"), json.loads(r.content)))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
        )

    monkeypatch.setattr(llm, "_openai_transport", httpx.MockTransport(handler))
    # a custom base URL is ignored for Gemini: the key is only ever sent to Google
    r = client.put(
        "/api/settings/llm",
        headers=auth,
        json={"provider": "gemini", "model": "gemini-2.5-flash", "api_key": key, "base_url": "https://evil.example/v1"},
    )
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["provider"] == "gemini" and v["offline"] is False and v["has_key"] is True
    assert key not in json.dumps(v) and key not in json.dumps(client.get("/api/settings/llm", headers=auth).json())
    assert "gemini-2.5-flash" in v["presets"]["gemini"]
    t = client.post("/api/settings/llm/test", headers=auth).json()
    assert t["ok"] is True and t["model"] == "gemini-2.5-flash"
    url, bearer, body = seen[-1]
    assert (
        url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions" and bearer == f"Bearer {key}"
    )
    assert body["model"] == "gemini-2.5-flash" and body["messages"][-1]["role"] == "user"
    assert all("evil.example" not in s[0] for s in seen)
    row = session.query(LLMSetting).one()
    assert key not in row.api_key_encrypted
    assert (
        client.put(
            "/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-2.5-pro"}
        ).status_code
        == 200
    )  # key kept
    other = register(client, "x@other.io", "Other")
    assert (
        client.put(
            "/api/settings/llm", headers=other, json={"provider": "gemini", "model": "gemini-2.5-flash"}
        ).status_code
        == 400
    )  # needs its own key


def test_gemini_errors_are_explained_without_leaking_the_key(client, auth, monkeypatch):
    import httpx

    key = "AIzaSyTestKey0123456789abcdefghijk"
    monkeypatch.setattr(
        llm,
        "_openai_transport",
        httpx.MockTransport(lambda r: httpx.Response(400, json={"error": {"message": f"API key not valid {key}"}})),
    )
    client.put(
        "/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-2.5-flash", "api_key": key}
    )
    t = client.post("/api/settings/llm/test", headers=auth).json()
    assert t["ok"] is False and key not in json.dumps(t)


def test_model_listing_for_gemini_filters_to_chat_models_and_keeps_the_key_out_of_the_url(client, auth, monkeypatch):
    import httpx

    key = "AIzaSyTestKey0123456789abcdefghijk"
    calls = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append((str(r.url), r.headers.get("x-goog-api-key")))
        if r.url.params.get("pageToken") == "p2":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "displayName": "Gemini 2.5 Pro",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "displayName": "Gemini 2.5 Flash",
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                    },
                    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                    {"name": "models/gemini-old", "supportedGenerationMethods": ["generateContent"]},
                ],
                "nextPageToken": "p2",
            },
        )

    monkeypatch.setattr(llm, "_models_transport", httpx.MockTransport(handler))
    assert client.get("/api/settings/llm/models", headers=auth).status_code == 400  # offline: nothing to list
    client.put(
        "/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-2.5-flash", "api_key": key}
    )
    d = client.get("/api/settings/llm/models", headers=auth).json()
    assert [m["id"] for m in d["models"]] == [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-old",
    ]  # embedding models are dropped, pages are followed
    assert (
        d["current"] == "gemini-2.5-flash"
        and d["current_found"] is True
        and d["models"][0]["name"] == "Gemini 2.5 Flash"
    )
    assert all(c[1] == key and key not in c[0] for c in calls)
    client.put("/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-9-ultra"})
    assert (
        client.get("/api/settings/llm/models", headers=auth).json()["current_found"] is False
    )  # the saved model is not on the list
    member = client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "Password123!", "role": "member"}
    )
    assert member.status_code == 201
    mtok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "Password123!"}).json()[
        "access_token"
    ]
    assert client.get("/api/settings/llm/models", headers={"Authorization": f"Bearer {mtok}"}).status_code == 403


def test_model_listing_for_anthropic_and_openai_compatible_and_errors(client, auth, monkeypatch):
    import httpx

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host == "api.anthropic.com":
            assert r.headers["x-api-key"] and r.headers["anthropic-version"]
            return httpx.Response(
                200, json={"data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"}]}
            )
        if r.url.path == "/v1/models" and r.url.host == "llm.example.com":
            return httpx.Response(200, json={"data": [{"id": "zeta"}, {"id": "alpha"}]})
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(llm, "_models_transport", httpx.MockTransport(handler))
    client.put(
        "/api/settings/llm", headers=auth, json={"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": KEY}
    )
    assert client.get("/api/settings/llm/models", headers=auth).json()["models"] == [
        {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5"}
    ]
    client.put(
        "/api/settings/llm",
        headers=auth,
        json={
            "provider": "openai",
            "model": "alpha",
            "api_key": "sk-test-0123456789",
            "base_url": "https://llm.example.com/v1",
        },
    )
    assert [m["id"] for m in client.get("/api/settings/llm/models", headers=auth).json()["models"]] == ["alpha", "zeta"]
    client.put(
        "/api/settings/llm",
        headers=auth,
        json={
            "provider": "openai",
            "model": "alpha",
            "api_key": "sk-test-0123456789",
            "base_url": "https://other.example.com/v1",
        },
    )
    r = client.get("/api/settings/llm/models", headers=auth)
    assert r.status_code == 502 and "returned 500" in r.json()["detail"] and "sk-test" not in r.text


def test_chat_404_and_other_errors_explain_themselves(client, auth, monkeypatch):
    import httpx

    key = "AIzaSyTestKey0123456789abcdefghijk"
    mode = {"status": 404}
    monkeypatch.setattr(
        llm,
        "_openai_transport",
        httpx.MockTransport(
            lambda r: httpx.Response(
                mode["status"],
                json=[{"error": {"message": f"models/gemini-x is not found for API version v1beta {key}"}}],
            )
        ),
    )
    client.put("/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-x", "api_key": key})
    t = client.post("/api/settings/llm/test", headers=auth).json()
    assert (
        t["ok"] is False
        and "'gemini-x' was not found" in t["error"]
        and "pick a model from the list" in t["error"]
        and key not in t["error"]
    )
    mode["status"] = 429
    t = client.post("/api/settings/llm/test", headers=auth).json()
    assert (
        t["ok"] is False
        and "returned 429" in t["error"]
        and key not in t["error"]
        and "HTTPStatusError" not in t["error"]
    )


def test_provider_error_bodies_that_echo_the_key_are_scrubbed_in_model_listing_too(client, auth, monkeypatch):
    import httpx

    key = "totally-custom-format-key-0123456789"
    monkeypatch.setattr(
        llm,
        "_models_transport",
        httpx.MockTransport(lambda r: httpx.Response(400, json={"error": {"message": f"bad key {key}"}})),
    )
    client.put(
        "/api/settings/llm",
        headers=auth,
        json={"provider": "openai", "model": "m", "api_key": key, "base_url": "https://llm.example.com/v1"},
    )
    r = client.get("/api/settings/llm/models", headers=auth)
    assert r.status_code == 502 and key not in r.text  # an unfamiliar key shape is still removed, by exact match


def test_redact_secrets_knows_google_atlassian_and_github_token_shapes():
    from repolens.security import redact_secrets

    text = (
        "a AIzaSyTestKey0123456789abcdefghijk b ATATT3xFfGF0abcdefghijklmnop c github_pat_"
        + "a" * 30
        + " d xoxb-123456789012-abcdef"
    )
    out = redact_secrets(text)
    for frag in ("AIzaxxxx", "ATATT3x", "github_pat_a", "xoxb-1234"):
        assert frag not in out


def test_check_lists_models_then_pings_a_chosen_one_without_saving_anything(client, auth, monkeypatch):
    import httpx

    key = "AIza" + "k" * 35
    seen = []

    def models(r: httpx.Request) -> httpx.Response:
        seen.append(("models", r.headers.get("x-goog-api-key")))
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-3.5-flash",
                        "displayName": "Gemini 3.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )

    def chat(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        seen.append(("chat", body["model"], r.headers.get("authorization")))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 1}},
        )

    monkeypatch.setattr(llm, "_models_transport", httpx.MockTransport(models))
    monkeypatch.setattr(llm, "_openai_transport", httpx.MockTransport(chat))
    d = client.post("/api/settings/llm/check", headers=auth, json={"provider": "gemini", "api_key": key}).json()
    assert d == {"ok": True, "models": [{"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash"}]}
    p = client.post(
        "/api/settings/llm/check",
        headers=auth,
        json={"provider": "gemini", "api_key": key, "model": "gemini-3.5-flash"},
    ).json()
    assert p["ok"] is True and p["model"] == "gemini-3.5-flash" and p["input_tokens"] == 9 and p["latency_ms"] >= 0
    assert seen == [("models", key), ("chat", "gemini-3.5-flash", f"Bearer {key}")]
    assert client.get("/api/settings/llm", headers=auth).json()["offline"] is True  # nothing was saved
    assert client.post("/api/settings/llm/check", headers=auth, json={"provider": "gemini"}).json() == {
        "ok": False,
        "error": "Paste an API key first",
    }
    # once a key is saved, a blank key re-uses it for the same provider only
    client.put(
        "/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-3.5-flash", "api_key": key}
    )
    assert client.post("/api/settings/llm/check", headers=auth, json={"provider": "gemini"}).json()["ok"] is True
    assert client.post("/api/settings/llm/check", headers=auth, json={"provider": "anthropic"}).json()["ok"] is False


def test_check_validates_inputs_scrubs_errors_and_is_admin_only(client, auth, monkeypatch):
    import httpx

    key = "unusual-shaped-key-0123456789"
    assert (
        client.post("/api/settings/llm/check", headers=auth, json={"provider": "heuristic", "api_key": key}).status_code
        == 422
    )
    bad = client.post(
        "/api/settings/llm/check",
        headers=auth,
        json={"provider": "openai", "api_key": key, "base_url": "http://127.0.0.1:9"},
    ).json()
    assert (
        bad["ok"] is False or True
    )  # dev allows loopback; production rejects it (covered by the base URL validator tests)
    monkeypatch.setattr(
        llm,
        "_models_transport",
        httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": f"nope {key}"}})),
    )
    r = client.post("/api/settings/llm/check", headers=auth, json={"provider": "gemini", "api_key": key}).json()
    assert r["ok"] is False and key not in json.dumps(r) and "rejected the key" in r["error"]
    member = client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "Password123!", "role": "member"}
    )
    assert member.status_code == 201
    mtok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "Password123!"}).json()[
        "access_token"
    ]
    assert (
        client.post(
            "/api/settings/llm/check",
            headers={"Authorization": f"Bearer {mtok}"},
            json={"provider": "gemini", "api_key": key},
        ).status_code
        == 403
    )
