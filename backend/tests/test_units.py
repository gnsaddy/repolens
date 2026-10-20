import pytest

from repolens.config import get_settings
from repolens.security import decrypt_secret, encrypt_secret, hash_password, redact_secrets, verify_password
from repolens.services import usage
from repolens.services.embeddings import cosine, hash_embed, tokenize
from repolens.services.jira import JiraError, adf_to_text, markdown_to_adf, validate_base_url
from repolens.services.parsing import parse_file, should_index
from repolens.services.retrieval import extract_stack_refs, query_terms


def test_tokenize_splits_identifiers():
    toks = tokenize("getUserProfile user_profile_service")
    assert {"get", "user", "profile", "service", "getuserprofile"} <= set(toks)


def test_hash_embedding_prefers_related_text():
    q = hash_embed("null pointer in get profile", 256)
    near = hash_embed("def get_profile(): return profile or null", 256)
    far = hash_embed("render the billing invoice pdf template", 256)
    assert cosine(q, near) > cosine(q, far)


def test_parse_python_symbols_and_calls():
    src = "class A:\n    def foo(self):\n        return helper(1)\n\ndef helper(x):\n    return x\n"
    chunks = {(c.symbol, c.parent): c for c in parse_file("a.py", src)}
    assert ("foo", "A") in chunks and ("helper", "") in chunks
    assert "helper" in chunks[("foo", "A")].calls
    assert chunks[("foo", "A")].start_line == 2


def test_parse_fallback_for_unknown_language():
    chunks = parse_file("notes.md", "# Title\n" + "line\n" * 100)
    assert chunks[0].chunk_type == "doc" and chunks[0].symbol == "Title"


def test_should_index_filters():
    assert not should_index("node_modules/x/index.js", 10, 1000)
    assert not should_index("a.png", 10, 1000)
    assert not should_index("big.py", 10_000, 1000)
    assert should_index("src/a.py", 10, 1000)


def test_stack_refs_python_and_js():
    refs = extract_stack_refs('File "src/a.py", line 12, in run\n  at foo (src/b.ts:7:3)')
    assert {"file": "src/a.py", "line": 12, "symbol": "run", "innermost": True} in refs
    assert any(r["file"] == "src/b.ts" and r["line"] == 7 for r in refs)


def test_query_terms_drop_stopwords():
    assert "the" not in query_terms("the handshake times out")
    assert "handshake" in query_terms("the handshake times out")


def test_password_and_secret_roundtrip():
    h = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", h) and not verify_password("nope", h)
    assert decrypt_secret(encrypt_secret("tok")) == "tok"


def test_redact_secrets():
    out = redact_secrets("api_key = abcdef123456 and ghp_" + "a" * 36 + " and sk-" + "b" * 30)
    assert "abcdef123456" not in out and "ghp_" not in out and "sk-b" not in out


def test_cost_uses_versioned_pricing(monkeypatch):
    monkeypatch.setenv("REPOLENS_PRICING_JSON", '{"m": {"input": 3.0, "output": 15.0, "cached_input": 0.3}}')
    get_settings.cache_clear()
    cost, ver = usage.estimate_cost("m", 1_000_000, 100_000, cached=500_000)
    assert cost == pytest.approx(0.5 * 3 + 0.5 * 0.3 + 0.1 * 15)
    assert ver == get_settings().pricing_version
    assert usage.estimate_cost("unknown", 10, 10) == (0.0, "unpriced")


def test_budget_enforced(session, monkeypatch):
    monkeypatch.setenv("REPOLENS_DAILY_BUDGET_USD", "1.0")
    get_settings.cache_clear()
    usage.check_budget(session, "org")
    ev = usage.record_event(session, "org", agent="x", provider="p", model="m", input_tokens=1)
    ev.estimated_cost = 2.0
    session.commit()
    with pytest.raises(usage.BudgetExceeded):
        usage.check_budget(session, "org")


def test_jira_helpers():
    adf = markdown_to_adf("## Title\ntext **b**\n```\ncode\n```")
    kinds = [n["type"] for n in adf["content"]]
    assert kinds == ["heading", "paragraph", "codeBlock"]
    assert "code" in adf_to_text(adf)
    assert validate_base_url("https://x.atlassian.net/") == "https://x.atlassian.net"
    with pytest.raises(JiraError):
        validate_base_url("ftp://x")


def test_jira_url_ssrf_blocked_outside_dev(monkeypatch):
    monkeypatch.setenv("REPOLENS_ENV", "prod")
    get_settings.cache_clear()
    for bad in ("http://jira.example.com", "https://127.0.0.1", "https://10.0.0.5", "https://localhost"):
        with pytest.raises(JiraError):
            validate_base_url(bad)


def test_innermost_frame_is_last_for_python_first_for_js():
    py = extract_stack_refs('File "a/api.py", line 9, in handler\nFile "a/svc.py", line 16, in get')
    assert [r.get("innermost", False) for r in py] == [False, True]  # outermost first -> failure is last
    js = extract_stack_refs("at inner (src/x.js:10:3)\n at outer (src/y.js:4:1)")
    inner = [r for r in js if r.get("innermost")]
    assert len(inner) == 1 and inner[0]["file"] == "src/x.js"  # innermost first -> failure is first
    assert len([r for r in js if r["file"] == "src/x.js" and r["line"] == 10]) == 1  # no duplicate generic match
