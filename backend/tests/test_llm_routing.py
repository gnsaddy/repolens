import json

import httpx
import pytest

from repolens.models import LLMConnection, LLMModel, LLMSetting
from repolens.services import llm, llm_router
from repolens.services.llm import LLMClient, LLMConfig
from repolens.services.llm_router import Route, Task, complexity, decide, plan
from tests.conftest import register

KEY_G, KEY_A = "g-key-0123456789abcdef", "a-key-0123456789abcdef"


def route(model, tier, price=None, provider="gemini"):
    return Route(LLMConfig(provider, model, "https://x", "k", "org", price, price), tier)


ROUTES = [route("flash-lite", "light", 0.1), route("flash", "standard", 0.5), route("pro", "strong", 5.0)]


def test_plain_tasks_go_light_with_savings_and_hard_ones_go_up():
    d = decide(ROUTES, Task("librarian"), savings=True)
    assert d.tier == "light" and d.chain[0].cfg.model == "flash-lite"
    assert decide(ROUTES, Task("librarian"), savings=False).tier == "light"  # already the lowest tier
    assert decide(ROUTES, Task("investigator"), savings=True).tier == "light"  # plain RCA is cheap too...
    hard = Task("investigator", prompt_chars=40_000, evidence=9, has_trace=True, repos=2, low_confidence=True)
    d = decide(ROUTES, hard, savings=True)
    assert d.tier == "strong" and d.chain[0].cfg.model == "pro" and "harder than usual" in d.why[0]
    assert decide(ROUTES, Task("investigator"), savings=False).tier == "standard"  # savings off: the task's own tier
    assert decide(ROUTES, Task("runbook-writer", prompt_chars=14_000), savings=True).tier == "standard"
    assert (
        decide(ROUTES, Task("investigator", has_trace=True), savings=True).tier == "standard"
    )  # one signal keeps its tier


def test_complexity_points_explain_themselves():
    pts, why = complexity(Task("x", prompt_chars=40_000, evidence=9, has_trace=True, repos=3, low_confidence=True))
    assert (
        pts == 6 and len(why) == 5 and any("stack trace" in w for w in why) and any("3 repositories" in w for w in why)
    )
    assert complexity(Task("x", prompt_chars=14_000))[0] == 1 and complexity(Task("x"))[0] == 0


def test_chain_prefers_the_wanted_tier_then_cheapest_then_nearest_stronger():
    routes = [
        route("a", "standard", 2.0),
        route("b", "standard", 1.0),
        route("c", "light", 0.1),
        route("d", "strong", 9.0),
        route("e", "standard", None),
    ]
    d = decide(routes, Task("doc-updater"), savings=False)
    assert [r.cfg.model for r in d.chain] == [
        "b",
        "a",
        "e",
        "d",
        "c",
    ]  # standard by price (unpriced last), then stronger, then lighter
    assert (
        decide([route("only", "light", 1)], Task("runbook-writer"), True).chain[0].cfg.model == "only"
    )  # one tier: no routing
    assert decide([], Task("x")).chain == []


def test_budget_pressure_and_forced_tier():
    assert decide(ROUTES, Task("investigator", prompt_chars=40_000, evidence=9), True, budget_low=True).tier == "light"
    assert decide(ROUTES, Task("librarian", prefer="strong"), True).tier == "strong"
    up = llm_router.stronger(ROUTES, ROUTES[0])
    assert up and up.cfg.model == "flash" and llm_router.stronger(ROUTES, ROUTES[2]) is None


def test_plan_lists_every_kind_with_the_model_it_would_use():
    rows = plan(ROUTES, savings=True)
    assert {r["kind"] for r in rows} >= {"librarian", "investigator", "runbook-writer"}
    assert all(r["model"] and r["tier"] and r["why"] for r in rows)
    assert plan([], True)[0]["model"] is None


# ------------------------------------------------------------------------------------------------ registry API
def add(client, auth, provider, key, models, **kw):
    r = client.post(
        "/api/settings/models/connections",
        headers=auth,
        json={"provider": provider, "api_key": key, "models": models, **kw},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_registry_keeps_keys_write_only_and_manages_models(client, auth, session):
    v = add(
        client,
        auth,
        "gemini",
        KEY_G,
        [{"model": "flash-lite", "tier": "light"}, {"model": "flash", "tier": "standard"}],
    )
    c = v["connections"][0]
    assert (
        c["provider"] == "gemini" and c["has_key"] is True and [m["tier"] for m in c["models"]] == ["light", "standard"]
    )
    assert KEY_G not in json.dumps(v) and v["multi"] is True and v["active"] == 2
    row = session.query(LLMConnection).one()
    assert KEY_G not in row.api_key_encrypted
    v = add(
        client,
        auth,
        "anthropic",
        KEY_A,
        [{"model": "claude-sonnet-5-5", "tier": "strong", "input_price": 3, "output_price": 15}],
    )
    assert len(v["connections"]) == 2 and v["active"] == 3
    # adding models to an existing provider needs no key again
    v = add(client, auth, "gemini", "", [{"model": "pro", "tier": "strong"}])
    assert [m["model"] for m in v["connections"][0]["models"]] == ["flash-lite", "flash", "pro"]
    assert (
        client.post(
            "/api/settings/models/connections", headers=auth, json={"provider": "openai", "models": [{"model": "m"}]}
        ).status_code
        == 400
    )
    mid = v["connections"][0]["models"][1]["id"]
    v = client.put(f"/api/settings/models/{mid}", headers=auth, json={"tier": "light", "enabled": False}).json()
    assert (
        v["connections"][0]["models"][1]["tier"] == "light"
        and v["connections"][0]["models"][1]["enabled"] is False
        and v["active"] == 3
    )
    assert client.put(f"/api/settings/models/{mid}", headers=auth, json={"tier": "huge"}).status_code == 422
    v = client.delete(f"/api/settings/models/{mid}", headers=auth).json()
    assert [m["model"] for m in v["connections"][0]["models"]] == ["flash-lite", "pro"]
    cid = v["connections"][1]["id"]
    v = client.delete(f"/api/settings/models/connections/{cid}", headers=auth).json()
    assert [c["provider"] for c in v["connections"]] == ["gemini"] and session.query(LLMModel).filter_by(
        model="claude-sonnet-5-5"
    ).count() == 0


def test_registry_is_admin_only_and_isolated(client, auth):
    add(client, auth, "gemini", KEY_G, [{"model": "flash"}])
    other = register(client, "x@other.io", "Other")
    assert client.get("/api/settings/models", headers=other).json()["connections"] == []
    mid = client.get("/api/settings/models", headers=auth).json()["connections"][0]["models"][0]["id"]
    assert client.put(f"/api/settings/models/{mid}", headers=other, json={"enabled": False}).status_code == 404
    assert client.delete(f"/api/settings/models/{mid}", headers=other).status_code == 404
    member = client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "Password123!", "role": "member"}
    )
    assert member.status_code == 201
    mtok = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "m@acme.io", "password": "Password123!"}).json()["access_token"]
    }
    assert (
        client.post(
            "/api/settings/models/connections",
            headers=mtok,
            json={"provider": "gemini", "api_key": "k" * 12, "models": [{"model": "m"}]},
        ).status_code
        == 403
    )
    assert client.put("/api/settings/routing", headers=mtok, json={"savings_mode": False}).status_code == 403
    assert client.get("/api/settings/models", headers=mtok).status_code == 200  # everyone can see what is in use


def test_existing_single_model_setup_is_adopted_not_lost(client, auth, session):
    client.put(
        "/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-2.5-flash", "api_key": KEY_G}
    )
    v = client.get("/api/settings/models", headers=auth).json()
    assert (
        v["connections"][0]["legacy"] is True
        and v["connections"][0]["models"][0]["model"] == "gemini-2.5-flash"
        and v["active"] == 1
    )
    v = add(client, auth, "anthropic", KEY_A, [{"model": "claude-haiku-5-5", "tier": "light"}])
    assert (
        sorted(c["provider"] for c in v["connections"]) == ["anthropic", "gemini"] and v["active"] == 2
    )  # the old model joined the registry
    assert session.query(LLMModel).filter_by(model="gemini-2.5-flash").one().tier == "standard"


def test_routing_toggle_changes_the_plan(client, auth):
    add(
        client,
        auth,
        "gemini",
        KEY_G,
        [{"model": "lite", "tier": "light"}, {"model": "mid", "tier": "standard"}, {"model": "big", "tier": "strong"}],
    )
    on = {r["kind"]: r["model"] for r in client.get("/api/settings/models", headers=auth).json()["plan"]}
    assert on["investigator"] == "lite" and on["runbook-writer"] == "lite"  # savings mode: one tier down
    off = {
        r["kind"]: r["model"]
        for r in client.put("/api/settings/routing", headers=auth, json={"savings_mode": False}).json()["plan"]
    }
    assert off["investigator"] == "mid" and off["librarian"] == "lite"
    assert client.get("/api/settings/models", headers=auth).json()["savings_mode"] is False


# ------------------------------------------------------------------------------------------------ calling
def test_client_routes_by_task_falls_back_on_errors_and_escalates_unusable_answers(client, auth, session, monkeypatch):
    add(
        client,
        auth,
        "gemini",
        KEY_G,
        [
            {"model": "lite", "tier": "light", "input_price": 0.1, "output_price": 0.4},
            {"model": "big", "tier": "strong", "input_price": 2, "output_price": 8},
        ],
    )
    calls = []
    mode = {"lite": "ok", "big": "ok"}

    def handler(r: httpx.Request) -> httpx.Response:
        model = json.loads(r.content)["model"]
        calls.append(model)
        if mode[model] == "down":
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        text = "not json at all" if mode[model] == "junk" else '{"ok": true}'
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    monkeypatch.setattr(llm, "_openai_transport", httpx.MockTransport(handler))
    org = session.query(LLMModel).first().org_id
    c = LLMClient(session, org)
    assert c.offline is False and c.cache_id.startswith("auto:")
    res = c.complete("librarian", "sys", "short question", task=Task("librarian"))
    assert calls == ["lite"] and res.route["tier"] == "light" and res.route["escalated"] is False
    # a hard task goes to the strong model
    calls.clear()
    c.complete(
        "investigator", "sys", "x" * 50_000, task=Task("investigator", prompt_chars=50_000, evidence=9, has_trace=True)
    )
    assert calls == ["big"]
    # the light model is down: the next model answers
    calls.clear()
    mode["lite"] = "down"
    res = c.complete("librarian", "sys", "q", task=Task("librarian"))
    assert calls == ["lite", "big"] and res.route["model"] == "big" and res.route["fallbacks"] == 1
    # the light model answers with junk: one stronger model is tried
    calls.clear()
    mode["lite"] = "junk"
    res = c.complete("librarian", "sys", "q", task=Task("librarian"), validate=lambda t: t.startswith("{"))
    assert calls == ["lite", "big"] and res.route["escalated"] is True and res.text == '{"ok": true}'
    # every model failing raises the last error
    mode["lite"] = mode["big"] = "down"
    with pytest.raises(RuntimeError, match="503"):
        c.complete("librarian", "sys", "q")
    # both models' calls are recorded separately, with their own prices
    from repolens.models import LLMCallEvent

    events = session.query(LLMCallEvent).filter_by(org_id=org).all()
    assert {e.model_name for e in events} == {"lite", "big"} and any(e.success is False for e in events)


def test_single_legacy_model_behaves_exactly_as_before(client, auth, session, monkeypatch):
    client.put(
        "/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "gemini-2.5-flash", "api_key": KEY_G}
    )
    seen = []
    monkeypatch.setattr(
        llm,
        "_openai_transport",
        httpx.MockTransport(
            lambda r: (
                seen.append(json.loads(r.content)["model"]),
                httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}}),
            )[1]
        ),
    )
    org = session.query(LLMSetting).one().org_id
    c = LLMClient(session, org)
    assert c.provider == "gemini" and c.model == "gemini-2.5-flash" and c.cache_id == "gemini/gemini-2.5-flash"
    c.complete("investigator", "s", "p", task=Task("investigator", prompt_chars=90_000, evidence=20, has_trace=True))
    assert seen == ["gemini-2.5-flash"]  # a single model is used for everything, however hard the task


def test_disabling_every_model_goes_offline_instead_of_using_the_old_setup(client, auth, session):
    client.put("/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "old", "api_key": KEY_G})
    v = add(client, auth, "anthropic", KEY_A, [{"model": "m"}])
    for c in v["connections"]:
        for m in c["models"]:
            client.put(f"/api/settings/models/{m['id']}", headers=auth, json={"enabled": False})
    org = session.query(LLMConnection).first().org_id
    assert LLMClient(session, org).offline is True
