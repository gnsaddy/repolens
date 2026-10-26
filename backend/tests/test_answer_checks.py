from repolens.models import Organization
from repolens.services import knowledge as kb
from repolens.services import name_check as nc
from tests.test_chat import new_chat, say
from tests.test_walkthrough import Spy, svc  # noqa: F401  (svc is a fixture)

REAL = "It is handled by `create_user()` in `auth.py` [S1], which calls `authorize()` in `policy.py`."
MADE_UP = REAL + " It then calls `grant_everyone_admin()` from `services/ghost.py` and `POST /api/auth/ghost`."


def test_extract_finds_paths_routes_and_functions_outside_code_blocks():
    text = (
        "See `backend/repolens/api/auth.py` and `POST /api/auth/users/{user_id}`, via `create_user()` or `Svc.run()`.\n"
        "```python\nfrom nowhere.py import fake_in_code()\n```\nNot a path: version 1.2.3 and https://example.com/x.md"
    )
    got = nc.extract(text)
    assert got["path"] == ["backend/repolens/api/auth.py"]
    assert got["route"] == ["/api/auth/users/{user_id}"]
    assert got["func"] == ["create_user", "Svc.run"]


def test_a_name_in_the_sources_or_the_repositories_is_fine_and_one_found_nowhere_is_reported(session, svc):  # noqa: F811
    org = session.query(Organization).first().id
    got = nc.unsupported(session, org, MADE_UP, [], "How are roles assigned?", "explain", None)
    assert got == [
        "services/ghost.py",
        "/api/auth/ghost",
        "grant_everyone_admin()",
    ]  # real names pass with no sources at all
    assert nc.unsupported(session, org, REAL, [], "q", "explain", None) == []


def test_names_the_person_typed_or_the_sources_show_are_not_reported(session, svc):  # noqa: F811
    org = session.query(Organization).first().id
    src = [{"title": "t", "file": "x.py", "content": "def grant_everyone_admin(): ..."}]
    assert nc.unsupported(session, org, "Call `grant_everyone_admin()`.", src, "q", "explain", None) == []
    assert (
        nc.unsupported(session, org, "Call `my_new_thing()`.", [], "what about my_new_thing()?", "explain", None) == []
    )


def test_build_requests_may_name_new_things(session, svc):  # noqa: F811
    org = session.query(Organization).first().id
    assert nc.unsupported(session, org, MADE_UP, [], "Write an API to list admins", "build", None) == []


def test_the_knowledge_box_reports_names_that_do_not_exist(client, auth, svc, monkeypatch):  # noqa: F811
    Spy(MADE_UP).install(monkeypatch)
    r = client.post(
        "/api/knowledge/ask", headers=auth, json={"query": "How does the user endpoint assign roles?"}
    ).json()
    assert r["unsupported_names"] == ["services/ghost.py", "/api/auth/ghost", "grant_everyone_admin()"]
    Spy(REAL).install(monkeypatch)
    r = client.post(
        "/api/knowledge/ask", headers=auth, json={"query": "How does the user endpoint assign roles?", "fresh": True}
    ).json()
    assert r.get("unsupported_names") == [] and "retrieval" in r, (r.get("mode"), sorted(r), r.get("answer", "")[:200])


def test_the_chat_tells_the_reader_which_names_to_check(client, auth, svc, monkeypatch):  # noqa: F811
    Spy(MADE_UP).install(monkeypatch)
    c = new_chat(client, auth)
    r = say(client, auth, c["id"], "How does the user endpoint assign roles?")
    text = r.json()["items"][-1]["content"]
    assert (
        "Check these names" in text
        and "`grant_everyone_admin()`" in text
        and "`create_user()`" not in text.split("Check these names")[1]
    )


def _hit(file, content):
    return {"title": file, "file": file, "content": content}


def test_a_word_cut_short_by_the_stemmer_still_matches_its_source_word():
    assert kb.coverage("how are tokens revoked", [_hit("a.py", "def revoke_token(): ...")]) == (1.0, [])


def test_coverage_counts_the_questions_key_words_in_the_best_sources():
    cov, missing = kb.coverage(
        "How are roles assigned to a user?", [_hit("auth.py", "def create_user(): role = body.role")]
    )
    assert missing == ["assign"] and 0 < cov < 1
    assert kb.coverage("hi", []) == (1.0, [])  # too little to judge


def test_a_second_wider_search_is_made_once_when_the_first_misses_key_words(session, svc, monkeypatch):  # noqa: F811
    org = session.query(Organization).first().id
    calls = []

    def fake_search(db, o, query, repo_ids, k=8, scope=None, kinds=None):
        calls.append((query, k))
        if "assigns" in query:
            return [_hit("auth.py", "assigns a role to the user")]
        return [_hit("other.py", "unrelated text")]

    monkeypatch.setattr(kb, "search", fake_search)
    results, info = kb.retrieve_checked(session, org, "how are roles assigned to a user", None, kind="lookup")
    assert [r["file"] for r in results] == ["auth.py"]
    assert info["refined"] is True and len(calls) == 2 and calls[1][1] > calls[0][1]


def test_no_second_search_when_the_first_covers_the_question_or_the_second_is_no_better(session, svc, monkeypatch):  # noqa: F811
    org = session.query(Organization).first().id
    calls = []

    def good(db, o, query, repo_ids, k=8, scope=None, kinds=None):
        calls.append(query)
        return [_hit("auth.py", "assigns a role to the user")]

    monkeypatch.setattr(kb, "search", good)
    kb.retrieve_checked(session, org, "how are roles assigned to a user", None, kind="lookup")
    assert len(calls) == 1

    calls.clear()

    def bad(db, o, query, repo_ids, k=8, scope=None, kinds=None):
        calls.append(query)
        return [_hit("other.py", "nothing relevant")]

    monkeypatch.setattr(kb, "search", bad)
    results, info = kb.retrieve_checked(session, org, "how are roles assigned to a user", None, kind="lookup")
    assert len(calls) == 2 and info["refined"] is False and results[0]["file"] == "other.py"
    assert info["missing"]  # said plainly, not hidden


def test_asking_fresh_twice_replaces_the_saved_answer_instead_of_failing(client, auth, svc, monkeypatch):  # noqa: F811
    """Full quality runs always ask fresh; the second run used to fail on the saved answer's unique key."""
    Spy("First answer. [S1]").install(monkeypatch)
    body = {"query": "How does the user endpoint assign roles?", "fresh": True}
    assert client.post("/api/knowledge/ask", headers=auth, json=body).json()["mode"] == "llm"
    Spy("Second answer. [S1]").install(monkeypatch)
    r = client.post("/api/knowledge/ask", headers=auth, json=body).json()
    assert r["mode"] == "llm" and r["answer"].startswith("Second answer")
