import httpx
import pytest

from repolens.config import get_settings
from repolens.services import rerank as rr
from repolens.services.embeddings import Embedder
from repolens.services.retrieval import search_chunks
from tests.test_api import add_repo


def test_proximity_rewards_adjacent_terms():
    close = "calculate the invoice total with tax".split()
    far = ["invoice"] + ["x"] * 30 + ["total"]
    assert rr._proximity(close, ["invoice", "total"]) > rr._proximity(far, ["invoice", "total"]) > 0
    assert rr._proximity(close, ["invoice"]) == 0.0  # one term has no proximity
    assert rr._proximity([], ["a", "b"]) == 0.0


def test_lexical_scores_prefer_the_chunk_that_answers_the_query():
    docs = [
        ("def compute_total(items):\n    return sum(i.price for i in items)", "billing.py compute_total"),
        ("def render_page(request):\n    return html", "views.py render_page"),
        ("# total of everything\nlogger.info('total')", "notes.py"),
    ]
    s = rr.lexical_scores(["compute", "total"], docs)
    assert s[0] == max(s) and s[1] == min(s) and s[0] > s[2]


def test_blend_keeps_first_stage_evidence_and_off_switch(monkeypatch):
    docs = [("alpha beta", "a"), ("alpha", "b")]
    assert rr.rerank("none", "q", ["alpha"], docs, [0.2, 0.1]) == [0.2, 0.1]
    out = rr.rerank("lexical", "alpha beta", ["alpha", "beta"], docs, [0.2, 0.1])
    assert out[0] > out[1]


@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv("REPOLENS_RERANK_PROVIDER", "api")
    monkeypatch.setenv("REPOLENS_RERANK_BASE_URL", "https://rerank.example/v1")
    monkeypatch.setenv("REPOLENS_RERANK_API_KEY", "k")
    get_settings.cache_clear()


def test_api_reranker_reorders_and_never_breaks_search(api_env, monkeypatch):
    calls = []

    def ok(url, headers, json, timeout):
        calls.append((url, json))
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.99}, {"index": 0, "relevance_score": 0.1}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", ok)
    docs = [("one", "a"), ("two", "b")]
    out = rr.rerank("api", "q", ["q"], docs, [0.9, 0.1])
    assert out[1] > out[0] and calls[0][0] == "https://rerank.example/v1/rerank" and calls[0][1]["top_n"] == 2

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    assert rr.rerank("api", "q", ["q"], docs, [0.9, 0.1]) == [0.9, 0.1]  # falls back to the fused order


def test_search_chunks_can_rerank_and_is_deterministic(client, auth, demo_repo, session):
    path, _ = demo_repo
    repo = add_repo(client, auth, path)
    from repolens.models import Organization

    org = session.query(Organization).first().id
    q = "profile endpoint returns 500 for deleted user"
    base = search_chunks(session, org, q, [repo["id"]], k=5, embedder=Embedder(), rerank="none")
    ranked = search_chunks(session, org, q, [repo["id"]], k=5, embedder=Embedder(), rerank="lexical")
    again = search_chunks(session, org, q, [repo["id"]], k=5, embedder=Embedder(), rerank="lexical")
    assert [h.chunk.id for h in ranked] == [h.chunk.id for h in again]
    assert {h.chunk.id for h in ranked} <= {
        h.chunk.id for h in search_chunks(session, org, q, [repo["id"]], k=30, embedder=Embedder(), rerank="none")
    }
    assert base and ranked


def test_retrieval_status_endpoint(client, auth):
    d = client.get("/api/settings/retrieval", headers=auth).json()
    assert (
        d["embedding"]["offline"] is True and d["rerank"]["provider"] == "lexical" and d["rerank"]["configured"] is True
    )
    assert "api_key" not in str(d).lower()  # no secrets
    assert client.get("/api/settings/retrieval").status_code == 401
