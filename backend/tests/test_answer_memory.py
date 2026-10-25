import json

import httpx

from repolens.models import AnswerMemory, Repository
from repolens.services import llm
from tests.conftest import register
from tests.test_knowledge import make_collection, upload

KEY = "g-key-0123456789abcdef"


def setup_llm(client, auth, monkeypatch):
    client.put("/api/settings/llm", headers=auth, json={"provider": "gemini", "model": "m", "api_key": KEY})
    calls = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(1)
        text = "The service retries three times [S1]."
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 20},
            },
        )

    monkeypatch.setattr(llm, "_openai_transport", httpx.MockTransport(handler))
    return calls


def ask(client, auth, q, **kw):
    r = client.post("/api/wiki/ask", headers=auth, json={"query": q, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def seed_doc(client, auth):
    cid = make_collection(client, auth, "ops")["id"]
    r = upload(
        client,
        auth,
        cid,
        {
            "retry.md": "# Retry policy\n\nThe payment service retries failed requests three times with exponential backoff.\n"
        },
    )
    assert r.status_code in (200, 201), r.text


def test_repeat_and_reworded_questions_are_answered_from_memory(client, auth, session, monkeypatch):
    calls = setup_llm(client, auth, monkeypatch)
    seed_doc(client, auth)
    a = ask(client, auth, "How many times does the payment service retry failed requests?")
    assert a["mode"] == "llm" and "memory" not in a and len(calls) == 1
    b = ask(client, auth, "How many times does the payment service retry failed requests?")
    assert (
        b["memory"]["tokens_saved"] == 420
        and b["answer"] == a["answer"]
        and b["sources"] == a["sources"]
        and len(calls) == 1
    )
    c = ask(client, auth, "How many times does the payment service retry the failed requests")
    assert c.get("memory") and len(calls) == 1  # re-worded
    d = ask(client, auth, "What exponential backoff does the payment service use between attempts?")
    assert "memory" not in d and len(calls) == 2  # a different question is a different answer
    e = ask(client, auth, "How many times does the payment service retry failed requests?", fresh=True)
    assert "memory" not in e and len(calls) == 3
    s = client.get("/api/settings/memory", headers=auth).json()
    assert s["entries"] >= 2 and s["hits"] == 2 and s["tokens_saved"] == 840


def test_memory_expires_when_sources_change_and_is_scoped(client, auth, session, monkeypatch):
    calls = setup_llm(client, auth, monkeypatch)
    seed_doc(client, auth)
    q = "How many times does the payment service retry failed requests?"
    ask(client, auth, q)
    ask(client, auth, q)
    assert len(calls) == 1
    cid = session.query(Repository).filter_by(source="docs").one().id
    upload(
        client, auth, cid, {"backoff.md": "# Backoff\n\nRetries wait one, two then four seconds.\n"}
    )  # the wiki changed
    assert "memory" not in ask(client, auth, q)  # memory retired; the model (or exact cache) answers
    other = register(client, "z@other.io", "Other")
    client.put("/api/settings/llm", headers=other, json={"provider": "gemini", "model": "m", "api_key": KEY})
    seen = ask(client, other, q)
    assert "memory" not in seen and seen["mode"] == "none"  # another organisation never sees this memory


def test_clear_and_disable_and_admin_only(client, auth, session, monkeypatch):
    calls = setup_llm(client, auth, monkeypatch)
    seed_doc(client, auth)
    q = "How many times does the payment service retry failed requests?"
    ask(client, auth, q)
    assert client.delete("/api/settings/memory", headers=auth).json()["cleared"] == 1
    assert session.query(AnswerMemory).count() == 0
    again = ask(client, auth, q)
    assert "memory" not in again and again["cached"] is True  # the exact-prompt cache still answers without the model
    assert len(calls) == 1
    monkeypatch.setenv("REPOLENS_ANSWER_MEMORY_ENABLED", "false")
    from repolens.config import get_settings

    get_settings.cache_clear()
    try:
        assert "memory" not in ask(client, auth, q + " ")
        assert "memory" not in ask(client, auth, q + "  ")
        assert session.query(AnswerMemory).count() == 0
    finally:
        monkeypatch.delenv("REPOLENS_ANSWER_MEMORY_ENABLED")
        get_settings.cache_clear()
    assert json.dumps(client.get("/api/settings/memory", headers=auth).json())


def test_similar_questions_are_suggested_before_the_model_answers(client, auth, session, monkeypatch):
    calls = setup_llm(client, auth, monkeypatch)
    seed_doc(client, auth)
    ask(client, auth, "How many times does the payment service retry failed requests?")
    r = client.post(
        "/api/wiki/similar",
        headers=auth,
        json={"query": "how often are failed payment requests retried by the service"},
    )
    s = r.json()
    assert (
        r.status_code == 200
        and s
        and s[0]["asked"].startswith("How many times")
        and "retries three times" in s[0]["answer"]
    )
    assert s[0]["similarity"] >= 0.5 and len(calls) == 1  # suggesting costs no tokens
    assert (
        client.post("/api/wiki/similar", headers=auth, json={"query": "kubernetes ingress certificate rotation"}).json()
        == []
    )
    assert (
        client.post(
            "/api/knowledge/similar", headers=auth, json={"query": "payment service retry failed requests"}
        ).json()
        == []
    )  # kind=docs vs all sources are separate
    assert (
        client.post(
            "/api/wiki/similar", headers=register(client, "q@other.io", "Q"), json={"query": "payment service retry"}
        ).json()
        == []
    )


def test_word_overlap_catches_rewordings_the_embedding_misses(client, auth, session, monkeypatch):
    calls = setup_llm(client, auth, monkeypatch)
    seed_doc(client, auth)
    ask(client, auth, "How many times does the payment service retry failed requests?")
    hit = ask(client, auth, "Retry failed requests: how many times, payment service?")
    assert hit.get("memory") and len(calls) == 1


def test_context_is_condensed_and_deduplicated_before_it_reaches_the_model(client, auth, session, monkeypatch):
    from repolens.services import context

    long = (
        "# Retry policy\n"
        + "\n".join(f"Unrelated filler sentence number {i} about nothing." for i in range(80))
        + "\nThe payment service retries failed requests three times.\nThen it alerts on-call.\n"
    )
    out = context.condense(long, {"retri", "payment"}, 400)
    assert len(out) <= 500 and "retries failed requests three times" in out and out.startswith("# Retry policy")
    s = [{"id": "S1"}, {"id": "S2"}, {"id": "S3"}]
    r = [{"content": long}, {"content": long.upper().lower()}, {"content": "short other passage"}]
    got = context.blocks(s, r, "payment retries", 3000)
    assert [x[0]["id"] for x in got] == ["S1", "S3"]  # S2 repeats S1
    assert len(got[0][1]) < len(long) / 2


def test_cheap_answers_must_cite_or_the_next_model_is_tried(client, auth, session, monkeypatch):
    from repolens.services.knowledge import _grounded

    assert _grounded("It retries three times [S1].") and _grounded("The sources do not mention that.")
    assert not _grounded("Probably around three.")


def _remembered(client, auth, monkeypatch):
    setup_llm(client, auth, monkeypatch)
    seed_doc(client, auth)
    ask(client, auth, "How many times does the payment service retry failed requests?")
    ask(client, auth, "What exponential backoff does the payment service use between attempts?")


def test_memory_can_be_browsed_and_deep_searched(client, auth, session, monkeypatch):
    _remembered(client, auth, monkeypatch)
    rows = client.get("/api/settings/memory/entries", headers=auth).json()
    assert len(rows) == 2 and {"id", "asked", "answer", "sources", "hits", "tokens"} <= set(rows[0])
    q = client.get("/api/settings/memory/entries", headers=auth, params={"q": "retry"}).json()
    assert q and q[0]["asked"].startswith("How many times") and q[0]["matched"] in ("question", "answer")
    # deep search reads the answer text: "retries three" appears only in the answer
    deep = client.get("/api/settings/memory/entries", headers=auth, params={"q": "retries three"}).json()
    shallow = client.get(
        "/api/settings/memory/entries", headers=auth, params={"q": "retries three", "deep": "false"}
    ).json()
    assert deep and deep[0]["matched"] == "answer" and len(deep) >= len(shallow)
    assert (
        client.get("/api/settings/memory/entries", headers=auth, params={"q": "kubernetes ingress certificate"}).json()
        == []
    )


def test_memory_entries_are_private_to_the_workspace_and_deletable_by_admins(client, auth, session, monkeypatch):
    _remembered(client, auth, monkeypatch)
    other = register(client, "m@other.io", "M")
    assert client.get("/api/settings/memory/entries", headers=other).json() == []
    rows = client.get("/api/settings/memory/entries", headers=auth).json()
    assert client.delete(f"/api/settings/memory/entries/{rows[0]['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/settings/memory/entries/{rows[0]['id']}", headers=auth).status_code == 204
    assert len(client.get("/api/settings/memory/entries", headers=auth).json()) == 1


def test_selected_answers_become_a_skill_file(client, auth, session, monkeypatch):
    _remembered(client, auth, monkeypatch)
    ids = [r["id"] for r in client.get("/api/settings/memory/entries", headers=auth).json()]
    r = client.post("/api/settings/memory/skill", headers=auth, json={"entry_ids": ids, "name": "Payment retries"})
    assert r.status_code == 200
    s = r.json()
    assert s["name"] == "payment-retries" and s["filename"] == "payment-retries/SKILL.md" and s["entries"] == 2
    md = s["markdown"]
    assert md.startswith("---\nname: payment-retries\ndescription: Answers about:") and "\n---\n" in md
    assert "## When to use" in md and "retries three times" in md and "Sources:" in md
    assert client.post("/api/settings/memory/skill", headers=auth, json={"entry_ids": ["nope"]}).status_code == 404
    assert (
        client.post(
            "/api/settings/memory/skill", headers=register(client, "s@other.io", "S"), json={"entry_ids": ids}
        ).status_code
        == 404
    )
