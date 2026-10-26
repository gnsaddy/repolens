import subprocess

from repolens.models import Organization
from repolens.services import knowledge as kb
from repolens.services import walkthrough as wt
from tests.test_api import add_repo

LIMITER = '''"""Counts attempts in a window."""
import time

_log: dict[str, list[float]] = {}


def hit(key, window=60):
    now = time.time()
    seen = [t for t in _log.get(key, []) if now - t < window]
    seen.append(now)
    _log[key] = seen
    return len(seen)


def blocked_for(key, limit=5, window=60):
    return 0 if hit(key, window) <= limit else window
'''
GUARD = "from . import ratelimit\n\n\ndef guard(request):\n    return ratelimit.blocked_for(request.client)\n"
DOCS = "\n".join(
    f"## Sign-in security {i}\n\nRate limiting protects sign-in. Rate limiting slows guessing. Sign-in rate limiting is on.\n"
    for i in range(12)
)


def make_repo(client, auth, tmp_path):
    d = tmp_path / "limiter"
    (d / "app").mkdir(parents=True)
    (d / "docs").mkdir()
    (d / "app" / "ratelimit.py").write_text(LIMITER)
    (d / "app" / "middleware.py").write_text(GUARD)
    (d / "docs" / "security.md").write_text(DOCS)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(d), *cmd], check=True)
    return add_repo(client, auth, str(d), name="limiter")


def test_neighbouring_words_are_offered_as_one_name():
    pairs = {j for j, _a, _b in wt.joined_pairs("How does sign-in rate limiting work?")}
    assert {"signin", "ratelimit", "ratelimiting"} <= pairs
    assert wt.joined_pairs("the") == []
    assert not any(
        j == "doesthe" for j, _a, _b in wt.joined_pairs("How does the system work")
    )  # filler words do not join


def test_only_a_joined_name_that_exists_in_the_index_is_searched_for(client, auth, session, tmp_path):
    make_repo(client, auth, tmp_path)
    org = session.query(Organization).first().id
    assert wt.joined_terms(session, org, "How does sign-in rate limiting work?") == ["ratelimit"]
    assert wt.joined_terms(session, org, "What is the weather today?") == []


def test_code_written_as_one_word_is_found_when_the_question_uses_two(client, auth, session, tmp_path):
    make_repo(client, auth, tmp_path)
    org = session.query(Organization).first().id
    got = kb.retrieve(session, org, "How does sign-in rate limiting work?", None)
    files = [r["file"] for r in got]
    assert "app/ratelimit.py" in files[:8], files


def test_a_joined_word_counts_for_both_of_its_parts():
    q = "How does sign-in rate limiting work?"
    hit = [{"title": "hit", "file": "app/ratelimit.py", "content": "def hit(key): ..."}]
    cov, missing = kb.coverage(q, hit)
    assert "rate" not in missing and "limit" not in missing, missing


def test_interleaving_keeps_the_best_main_match_first_and_adds_at_most_five():
    main = [{"chunk_id": f"m{i}"} for i in range(8)]
    extra = [{"chunk_id": f"x{i}"} for i in range(5)] + [{"chunk_id": "m1"}]
    out = kb._with_joined(main, extra, 11)
    ids = [r["chunk_id"] for r in out]
    assert ids[0] == "m0" and ids[1] == "x0" and len(ids) == 11
    assert sum(1 for i in ids if i.startswith("x")) == 5 and ids.count("m1") == 1
