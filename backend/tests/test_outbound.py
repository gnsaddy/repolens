from repolens import config
from tests.conftest import register
from tests.test_llm_routing import KEY_G, add, reachable_models  # noqa: F401  (fixture)


def outbound(client, h):
    return client.get("/api/settings/outbound", headers=h).json()


def test_a_fresh_workspace_sends_nothing_anywhere(client, auth):
    o = outbound(client, auth)
    assert o["items"] == [] and o["external"] == 0 and o["summary"] == "Nothing leaves this server."
    assert any("run on this server only" in x for x in o["local_only"])


def test_every_connected_model_and_connector_is_listed_with_what_it_receives(client, auth, monkeypatch):
    add(client, auth, "gemini", KEY_G, [{"model": "flash"}, {"model": "pro", "tier": "strong"}])
    client.put(
        "/api/jira/connection",
        headers=auth,
        json={
            "base_url": "https://acme.atlassian.net",
            "deployment": "cloud",
            "email": "b@acme.io",
            "token": "tok-123456789",
            "allow_publish": False,
        },
    )
    o = outbound(client, auth)
    names = {i["name"]: i for i in o["items"]}
    g = names["Language model: gemini"]
    assert g["host"] == "generativelanguage.googleapis.com" and g["stays_on_machine"] is False
    assert any("questions" in x for x in g["sends"]) and "flash" in g["note"] and "Models and routing" in g["where"]
    assert names["Jira"]["host"] == "acme.atlassian.net" and "nothing is written" in names["Jira"]["note"]
    assert o["external"] == 2 and "2 connections send data off this server" in o["summary"]
    assert "tok-123456789" not in str(o) and KEY_G not in str(o)  # never a secret


def test_a_local_model_server_counts_as_staying_on_the_machine(client, auth, monkeypatch):
    add(client, auth, "openai", "k" * 12, [{"model": "llama3"}], base_url="http://localhost:11434/v1")
    o = outbound(client, auth)
    local = next(i for i in o["items"] if i["kind"] == "model")
    assert local["stays_on_machine"] is True and o["external"] == 0 and o["summary"] == "Nothing leaves this server."


def test_embedding_and_trace_destinations_appear_only_when_configured(client, auth, monkeypatch):
    monkeypatch.setenv("REPOLENS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("REPOLENS_EMBEDDING_BASE_URL", "https://api.example-embed.com/v1")
    monkeypatch.setenv("REPOLENS_OTLP_ENDPOINT", "http://collector.internal:4318/v1/traces")
    config.get_settings.cache_clear()
    kinds = {i["kind"]: i for i in outbound(client, auth)["items"]}
    assert kinds["embedding"]["host"] == "api.example-embed.com" and any(
        "every document" in x for x in kinds["embedding"]["sends"]
    )
    assert kinds["telemetry"]["host"] == "collector.internal:4318"


def test_each_workspace_sees_only_its_own_connections(client, auth):
    add(client, auth, "gemini", KEY_G, [{"model": "flash"}])
    assert outbound(client, register(client, "o@other.io", "O"))["items"] == []
