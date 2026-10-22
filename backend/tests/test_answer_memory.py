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
