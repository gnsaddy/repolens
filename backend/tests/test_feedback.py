from tests.conftest import register


def give(client, h, **kw):
    body = {"kind": "answer", "rating": "up", "target_id": "q1", "query": "How many retries?", **kw}
    return client.post("/api/feedback", headers=h, json=body)


def test_a_verdict_is_recorded_once_per_person_and_answer_and_can_change(client, auth):
    assert give(client, auth).status_code == 201
    assert give(client, auth, rating="down", note="missed the backoff").status_code == 201  # replaces, does not add
    assert give(client, auth, target_id="q2", meta={"from_memory": True, "evil": {"nested": 1}}).status_code == 201
    s = client.get("/api/feedback/summary", headers=auth).json()
    assert s["total"] == 2 and s["by_kind"]["answer"] == {"up": 1, "down": 1} and s["helpful_rate"] == 0.5
    assert s["from_memory"] == {"total": 1, "helpful_rate": 1.0}


def test_feedback_is_validated_redacted_and_scoped_to_the_workspace(client, auth):
    assert give(client, auth, rating="meh").status_code == 422 and give(client, auth, kind="other").status_code == 422
    give(client, auth, query="why does token ghp_" + "a" * 36 + " fail?")
    rows = client.get("/api/feedback/export", headers=auth, params={"rating": "up"}).json()
    assert rows and "ghp_" not in rows[0]["query"]
    other = register(client, "f@other.io", "F")
    assert client.get("/api/feedback/summary", headers=other).json()["total"] == 0
    assert client.get("/api/feedback/export", headers=other, params={"rating": "up"}).json() == []


def test_everyone_can_rate_but_only_admins_can_export(client, auth):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"}
    )
    v = {
        "Authorization": "Bearer "
        + client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()[
            "access_token"
        ]
    }
    assert give(client, v, rating="down").status_code == 201
    assert client.get("/api/feedback/export", headers=v).status_code == 403
    assert len(client.get("/api/feedback/export", headers=auth).json()) == 1


def test_a_change_of_embedding_model_is_noticed_and_the_reembed_tool_resets_it(
    client, auth, demo_repo, session, monkeypatch
):
    from repolens import config
    from repolens.devtools import reembed
    from repolens.services import embedding_state
    from tests.test_api import add_repo

    add_repo(client, auth, demo_repo[0])  # first index records what made the vectors
    st = client.get("/api/settings/retrieval", headers=auth).json()["embedding_state"]
    assert st["stale"] is False and st["indexed_with"]["model"] == "local-hash-v1"
    monkeypatch.setenv("REPOLENS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("REPOLENS_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("REPOLENS_EMBEDDING_DIM", "768")
    config.get_settings.cache_clear()
    st = client.get("/api/settings/retrieval", headers=auth).json()["embedding_state"]
    assert (
        st["stale"] is True
        and "nomic-embed-text" in st["message"]
        and "256" in st["message"]
        and st["current"]["dim"] == 768
    )
    assert embedding_state.status(
        session, session.query(__import__("repolens.models", fromlist=["Repository"]).Repository).first().org_id
    )["stale"]
    lines = reembed.run(dry=True)  # on SQLite there is nothing to resize; a dry run changes nothing
    assert any("SQLite" in x for x in lines)


def test_embeddings_work_with_keyless_local_servers(monkeypatch):
    import httpx

    from repolens import config
    from repolens.services import embeddings

    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["headers"] = headers
        req = httpx.Request("POST", url)
        return httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in json["input"]], "usage": {}}, request=req
        )

    monkeypatch.setenv("REPOLENS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("REPOLENS_EMBEDDING_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("REPOLENS_EMBEDDING_API_KEY", "")
    config.get_settings.cache_clear()
    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    assert embeddings.Embedder().embed(["a", "b"]) and seen["headers"] == {}  # no "Bearer " header with an empty key
    monkeypatch.setenv("REPOLENS_EMBEDDING_API_KEY", "k-123")
    config.get_settings.cache_clear()
    embeddings.Embedder().embed(["a"])
    assert seen["headers"] == {"Authorization": "Bearer k-123"}


def test_the_fast_local_embedder_produces_exactly_the_vectors_stored_vectors_were_made_with():
    """`local-hash-v1` must never change silently: stored vectors would stop matching new queries."""
    import hashlib

    from repolens.services.embeddings import hash_embed

    v = hash_embed("def get_profile(self): return UserRepository.find_by_id(user_id)  # KeyError: 'name'", 256)
    assert (
        hashlib.sha256(",".join(f"{x:.9f}" for x in v).encode()).hexdigest()
        == "8f63a282590f04eebcf61466ea85946ab56ec1df676e4d7f82f851e39a21a065"
    )
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9 and len(v) == 256
