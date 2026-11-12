"""Agent personas: a specialist reviews code, and only findings that quote real code are kept."""

import json

import pytest

from repolens.config import get_settings
from repolens.models import PersonaRun, Repository
from repolens.services import llm
from repolens.services import personas as svc
from repolens.services.llm import LLMResult
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_groups import (
    days_ago,
    libs,  # noqa: F401  (fixture: the shop and the library it imports)
)
from tests.test_risk import commit, git

AUTH = (
    "import sqlite3\n\n\n"
    "def find_user(db, name):\n"
    '    cur = db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")\n'
    "    return cur.fetchone()\n\n\n"
    "def total(items, discount=0):\n    return sum(items) - discount\n"
)
REQUEST = "the user lookup in auth and how find_user builds its database query"

SECURITY = {
    "summary": "find_user builds SQL by joining the name into the text.",
    "findings": [
        {
            "title": "SQL injection in find_user",
            "severity": "high",
            "file": "app/auth.py",
            "evidence": 'cur = db.execute("SELECT * FROM users WHERE name = \'" + name + "\'")',
            "explanation": "name comes from the caller and is joined into the query.",
            "suggestion": "Pass the name as a parameter.",
            "code": "cur = db.execute('SELECT * FROM users WHERE name = ?', (name,))",
        },
        {
            "title": "Invented problem",
            "severity": "high",
            "file": "app/auth.py",
            "evidence": "password = request.args['password']  # stored in plain text",
            "explanation": "not in the code",
            "suggestion": "",
        },
        {"title": "Wrong file", "severity": "medium", "file": "app/other.py", "evidence": "x = 1", "explanation": ""},
    ],
}
TESTS = {
    "summary": "total has no test for a discount larger than the sum.",
    "findings": [
        {
            "title": "total with a discount over the sum",
            "severity": "medium",
            "file": "app/auth.py",
            "evidence": "return sum(items) - discount",
            "explanation": "The result goes negative.",
            "suggestion": "Assert what should happen.",
            "code": "def test_total_never_negative():\n    assert total([1], 5) >= 0\n",
        },
        {
            "title": "broken sample",
            "severity": "low",
            "file": "app/auth.py",
            "evidence": "def total(items, discount=0):",
            "explanation": "x",
            "suggestion": "y",
            "code": "def test_(:\n",
        },
    ],
}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    state = type("M", (), {"reply": SECURITY, "calls": 0, "fail": None, "systems": []})()

    def fake(system, prompt, max_tokens, cfg=None):
        state.calls += 1
        state.systems.append(system)
        assert "untrusted" in system and "FILE app/auth.py" in prompt
        if state.fail:
            raise RuntimeError(state.fail)
        return LLMResult(state.reply if isinstance(state.reply, str) else json.dumps(state.reply), 1500, 400)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    yield state
    get_settings.cache_clear()


@pytest.fixture
def repo(client, auth, tmp_path):
    p = tmp_path / "shop"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(p, {"app/auth.py": AUTH, "README.md": "# shop\n"}, "feat: shop", days_ago(3))
    return add_repo(client, auth, p, name="shop")


def run(client, h, repo, persona="security-auditor", request=REQUEST, **extra):
    body = {"repo_id": repo["id"], "persona": persona, "request": request, **extra}
    return client.post("/api/personas/runs", headers=h, json=body)


def test_nothing_about_personas_is_available_without_signing_in(client):
    for method, url in (("get", "/api/personas"), ("get", "/api/personas/runs"), ("post", "/api/personas/runs")):
        assert getattr(client, method)(url).status_code == 401


def test_the_personas_are_listed_with_what_each_one_does(client, auth):
    got = client.get("/api/personas", headers=auth).json()["personas"]
    assert [p["key"] for p in got] == ["security-auditor", "test-engineer", "architect", "developer"]
    assert all(p["summary"] and p["placeholder"] for p in got)


def test_a_finding_is_kept_only_when_it_quotes_code_that_is_in_the_file(client, auth, repo, model):
    r = run(client, auth, repo)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "done" and d["persona_name"] == "Security auditor" and model.calls == 1
    (f,) = d["findings"]
    assert f["title"] == "SQL injection in find_user" and f["severity"] == "high" and f["file"] == "app/auth.py"
    assert f["line"] == 5 and "SELECT * FROM users" in f["evidence"]  # the line comes from the code, not the model
    assert d["notes"]["dropped"] == 2 and len(d["notes"]["reasons"]) == 2
    assert any("not in app/auth.py" in why for why in d["notes"]["reasons"])
    assert any("not shown" in why for why in d["notes"]["reasons"])
    assert d["notes"]["coverage"] == [{"file": "app/auth.py", "whole": True}] and "not run" in d["notes"]["limit"]
    assert d["tokens"] == {"calls": 1, "input_tokens": 1500, "output_tokens": 400, "cost": d["tokens"]["cost"]}
    assert "security reviewer" in model.systems[0]


def test_the_test_engineer_is_another_stance_and_suggested_test_code_is_checked_for_syntax(client, auth, repo, model):
    model.reply = TESTS
    d = run(client, auth, repo, "test-engineer").json()
    assert "test engineer" in model.systems[0] and [f["code_ok"] for f in d["findings"]] == [True, False]
    assert d["findings"][0]["severity"] == "medium" and d["persona_name"] == "Test engineer"


def test_an_unknown_persona_a_short_request_and_no_model_are_refused_plainly(client, auth, repo, model, monkeypatch):
    assert run(client, auth, repo, "poet").status_code == 409
    assert (
        client.post(
            "/api/personas/runs", headers=auth, json={"repo_id": repo["id"], "persona": "x", "request": "no"}
        ).status_code
        == 422
    )
    assert model.calls == 0
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "heuristic")
    get_settings.cache_clear()
    r = run(client, auth, repo)
    get_settings.cache_clear()
    assert r.status_code == 409 and "needs a language model" in r.json()["detail"]


def test_a_failed_model_call_is_reported_and_a_request_naming_no_code_reads_the_main_files(
    client, auth, repo, model, session
):
    model.fail = "overloaded"
    r = run(client, auth, repo)
    assert r.status_code == 409 and "could not write the review" in r.json()["detail"]
    assert session.query(PersonaRun).one().status == "failed"
    model.fail, model.calls = None, 0
    r = run(client, auth, repo, request="zebra giraffe telescope orchestration for the moon base")
    assert r.status_code == 201 and model.calls == 1  # nothing matched, so it read the repository's main files instead
    assert r.json()["notes"]["main_files"] == ["app/auth.py"]


def test_the_same_review_in_other_words_is_reused_but_another_persona_or_fresh_is_not(client, auth, repo, model):
    first = run(client, auth, repo).json()
    again = run(
        client, auth, repo, request="how find_user builds its database query, in the user lookup of auth"
    ).json()
    assert again["id"] == first["id"] and again["reused"] and model.calls == 1
    model.reply = TESTS
    other = run(client, auth, repo, "test-engineer").json()
    assert other["id"] != first["id"] and not other["reused"] and model.calls == 2
    fresh = run(client, auth, repo, fresh=True).json()
    assert fresh["id"] != first["id"] and model.calls == 3


def test_a_review_is_private_to_its_author_until_shared_and_only_the_author_changes_it(client, auth, repo, model):
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    assert run(client, viewer, repo).status_code == 403
    mine = run(client, ann, repo).json()
    url = f"/api/personas/runs/{mine['id']}"
    for other in (bob, auth):
        assert client.get(url, headers=other).status_code == 404
        assert client.get("/api/personas/runs", headers=other).json()["runs"] == []
    assert client.patch(url, headers=ann, json={"shared": True}).json()["shared"] is True
    for reader in (bob, viewer, auth):
        seen = client.get(url, headers=reader).json()
        assert seen["id"] == mine["id"] and seen["mine"] is False and seen["author_name"] == "ann"
        assert [r["id"] for r in client.get("/api/personas/runs", headers=reader).json()["runs"]] == [mine["id"]]
    assert client.patch(url, headers=bob, json={"shared": False}).status_code == 403
    assert client.delete(url, headers=bob).status_code == 403
    assert client.patch(url, headers=ann, json={"shared": False}).json()["shared"] is False
    assert client.get(url, headers=bob).status_code == 404
    assert client.delete(url, headers=ann).status_code == 204


def test_a_shared_review_of_a_repository_someone_cannot_open_is_not_found(client, auth, repo, model):
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    made = run(client, ann, repo).json()
    client.patch(f"/api/personas/runs/{made['id']}", headers=ann, json={"shared": True})
    allowed = [client.get("/api/auth/me", headers=ann).json()["id"]]
    client.put(
        f"/api/repositories/{repo['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": allowed}
    )
    assert client.get(f"/api/personas/runs/{made['id']}", headers=bob).status_code == 404
    assert client.get("/api/personas/runs", headers=bob).json()["runs"] == []


def test_deleting_a_repository_removes_its_reviews(client, auth, repo, model, session):
    run(client, auth, repo)
    assert client.delete(f"/api/repositories/{repo['id']}", headers=auth).status_code in (200, 204)
    session.expire_all()
    assert session.query(PersonaRun).count() == 0


def test_verify_forgives_whitespace_reads_several_lines_and_ranks_by_severity():
    text = "def a():\n    x = 1\n    return x\n\n\ndef b():\n    y = 2\n    return y\n"
    texts = {"m.py": text}

    def f(title, evidence, sev="low", file="m.py"):
        return {"title": title, "severity": sev, "file": file, "evidence": evidence}

    kept, why = svc.verify(
        [
            f("one", "return   y", "low"),
            f("two", "def b():\n  y = 2", "high"),
            f("none", ""),
            f("odd", "return x", "bogus"),
        ],
        texts,
    )
    assert [(k["title"], k["line"], k["severity"]) for k in kept] == [
        ("two", 6, "high"),
        ("one", 8, "low"),
        ("odd", 3, "info"),
    ]
    assert len(why) == 1 and "none: the quoted code is not in m.py" in why[0]
    assert len(svc.verify([f(str(i), "return x") for i in range(40)], texts)[0]) == svc.MAX_FINDINGS
    assert svc.verify("not a list", texts) == ([], [])


REVIEW = {
    "summary": "get_user is used by another service.",
    "findings": [
        {
            "title": "A shared function with no contract",
            "severity": "medium",
            "file": "userlib/core.py",
            "evidence": "def get_user(user_id):",
            "explanation": "shop-app calls it, so changing the arguments breaks that service.",
            "suggestion": "Version the function or accept the new argument as optional.",
        }
    ],
}


@pytest.fixture
def model_for_libs(monkeypatch):
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    state = type("M", (), {"calls": 0, "prompts": [], "systems": []})()

    def fake(system, prompt, max_tokens, cfg=None):
        state.calls += 1
        state.prompts.append(prompt)
        state.systems.append(system)
        return LLMResult(json.dumps(REVIEW), 1000, 200)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    yield state
    get_settings.cache_clear()


LIB_REQUEST = "the get_user function in the userlib core and who relies on it"


def test_the_architect_is_told_which_other_repositories_use_the_code_and_says_so(client, auth, libs, model_for_libs):  # noqa: F811
    shop, lib = libs
    r = run(client, auth, lib, "architect", LIB_REQUEST)
    assert r.status_code == 201, r.text
    d = r.json()
    prompt = model_for_libs.prompts[0]
    assert "DEPENDENCIES (proven by the code):" in prompt and "userlib/core.py is used by shop-app" in prompt
    assert "software architect" in model_for_libs.systems[0]
    facts = d["notes"]["dependencies"]["facts"]
    assert facts and all(f["repo"] == "shop-app" and f["direction"] == "used_by" for f in facts)
    assert d["findings"][0]["file"] == "userlib/core.py" and d["findings"][0]["line"] == 1


def test_the_other_personas_are_not_given_dependencies_they_do_not_need(client, auth, libs, model_for_libs):  # noqa: F811
    shop, lib = libs
    d = run(client, auth, lib, "developer", LIB_REQUEST).json()
    assert "DEPENDENCIES" not in model_for_libs.prompts[0] and "senior developer" in model_for_libs.systems[0]
    assert "dependencies" not in d["notes"] and d["persona_name"] == "Developer"


def test_a_shared_architect_review_does_not_name_a_repository_the_reader_cannot_open(
    client, auth, libs, model_for_libs
):  # noqa: F811
    shop, lib = libs
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    made = run(client, ann, lib, "architect", LIB_REQUEST).json()
    url = f"/api/personas/runs/{made['id']}"
    client.patch(url, headers=ann, json={"shared": True})
    ann_id = client.get("/api/auth/me", headers=ann).json()["id"]
    body = {"visibility": "restricted", "user_ids": [ann_id]}
    client.put(f"/api/repositories/{shop['id']}/access", headers=auth, json=body)
    seen = client.get(url, headers=bob).json()
    assert seen["notes"]["dependencies"]["facts"] == [] and seen["notes"]["dependencies"]["hidden"] >= 1
    assert "shop-app" not in json.dumps({k: v for k, v in seen["notes"].items()})
    assert client.get(url, headers=ann).json()["notes"]["dependencies"]["facts"]  # the author can still see them


DESIGN = {
    "summary": "A small service that looks users up.",
    "hld": "## Purpose\nLooks users up in a database.",
    "lld": "## auth\nfind_user builds a query; total sums prices.",
    "diagram": "```mermaid\nflowchart LR\n  A[auth] --> B[(db)]\n```",
    "components": [
        {"name": "auth", "responsibility": "looks users up", "files": ["app/auth.py"]},
        {"name": "ghost", "responsibility": "not in the code", "files": ["app/none.py"]},
    ],
    "flows": [
        {
            "name": "lookup",
            "steps": ["find_user builds the query", "fetchone returns the row"],
            "files": ["app/auth.py", "nope.py"],
        },
        {"name": "empty", "steps": [], "files": ["app/auth.py"]},
    ],
    "not_covered": ["the web layer was not shown"],
}


def design_text(d):
    """A design as the model is asked to write it: plain sections, not JSON."""
    comps = "\n".join(f"- {c['name']} | {c['responsibility']} | {', '.join(c['files'])}" for c in d["components"])
    flows = "\n".join(
        f"FLOW: {f['name']} | {', '.join(f['files'])}\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(f["steps"], 1))
        for f in d["flows"]
    )
    notes = "\n".join(f"- {n}" for n in d["not_covered"])
    return (
        f"=== SUMMARY ===\n{d['summary']}\n=== HLD ===\n{d['hld']}\n=== LLD ===\n{d['lld']}\n"
        f"=== DIAGRAM ===\n{d['diagram']}\n=== COMPONENTS ===\n{comps}\n=== FLOWS ===\n{flows}\n"
        f"=== NOT COVERED ===\n{notes}\n"
    )


def test_a_design_written_as_text_survives_quotes_code_and_missing_sections():
    text = (
        'Here is the design.\n=== SUMMARY ===\nIt says "hello" a lot.\n=== HLD ===\n'
        '## Purpose\nUses `db.execute("x")` and {braces}.\n\n```python\nprint("a, b")\n```\n'
        "=== COMPONENTS ===\n* `store` | keeps rows | `app/store.py`, app/db.py\nnot a component line\n"
        "=== FLOWS ===\nFLOW: read | app/store.py\n1) open\n- fetch\n"
    )
    got = svc.parse_design(text)
    assert got["summary"] == 'It says "hello" a lot.' and 'print("a, b")' in got["hld"] and got["lld"] == ""
    assert got["components"] == [
        {"name": "store", "responsibility": "keeps rows", "files": ["app/store.py", "app/db.py"]}
    ]
    assert got["flows"] == [{"name": "read", "files": ["app/store.py"], "steps": ["open", "fetch"]}]
    assert svc.parse_design("no headings at all")["hld"] == "" and svc.parse_design("")["components"] == []


def test_the_architect_can_write_a_design_overview_and_only_what_names_shown_files_is_kept(client, auth, repo, model):
    model.reply = design_text(DESIGN)
    r = run(client, auth, repo, "architect", "Explain the HLD and LLD", mode="design")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["mode"] == "design" and d["status"] == "done" and d["findings"] == [] and model.calls == 1
    design = d["notes"]["design"]
    assert "## Purpose" in design["hld"] and "find_user" in design["lld"]
    assert design["diagram"] == "flowchart LR\n  A[auth] --> B[(db)]"  # the fence is taken off
    assert [c["name"] for c in design["components"]] == ["auth"]  # "ghost" names a file that was not shown
    assert [(f["name"], f["files"]) for f in design["flows"]] == [("lookup", ["app/auth.py"])]
    assert design["not_covered"] == ["the web layer was not shown"] and d["notes"]["dropped"] == 2
    assert "design overview" in model.systems[0] and "software architect" in model.systems[0]


def test_a_design_overview_reads_the_main_files_and_a_review_of_the_same_words_is_not_reused_for_it(
    client, auth, repo, model
):
    review = run(client, auth, repo, "architect", "Explain the HLD and LLD").json()  # nothing in the code matches
    assert review["mode"] == "review" and review["notes"]["main_files"] == ["app/auth.py"]
    model.reply = design_text(DESIGN)
    first = run(client, auth, repo, "architect", "Explain the HLD and LLD", mode="design").json()
    assert first["id"] != review["id"] and not first["reused"] and model.calls == 2
    again = run(client, auth, repo, "architect", "explain the HLD and the LLD", mode="design").json()
    assert again["id"] == first["id"] and again["reused"] and model.calls == 2


OVERVIEWS = {  # persona: (the choice on the page, a phrase its instruction must carry)
    "architect": ("Design overview (HLD and LLD)", "software architect"),
    "security-auditor": ("Threat model", "threat model"),
    "test-engineer": ("Test plan", "test plan"),
    "developer": ("Code walkthrough", "walkthrough"),
}


def test_every_persona_can_describe_the_repository_in_its_own_way_and_the_list_says_so(client, auth, repo, model):
    listed = {p["key"]: p for p in client.get("/api/personas", headers=auth).json()["personas"]}
    assert all(p["modes"] == ["review", "design"] for p in listed.values())
    assert {k: p["overview"]["label"] for k, p in listed.items()} == {k: v[0] for k, v in OVERVIEWS.items()}
    assert listed["security-auditor"]["overview"]["first"] == "Threat model"
    assert listed["developer"]["overview"]["second"] == "Where to change what"
    model.reply = design_text(DESIGN)
    for key, (_, phrase) in OVERVIEWS.items():
        d = run(client, auth, repo, key, "Describe it", mode="design").json()
        assert d["mode"] == "design" and d["notes"]["design"]["components"][0]["name"] == "auth"
        assert phrase in model.systems[-1] and "=== HLD ===" in model.systems[-1]  # each asks in its own words
    assert model.calls == 4 and len({sys for sys in model.systems}) == 4
    assert run(client, auth, repo, "architect", "Explain the HLD and LLD", mode="poem").status_code == 422


def test_a_diagram_that_could_act_is_left_out_and_odd_model_output_does_not_break_the_design(client, auth, repo, model):
    model.reply = design_text({**DESIGN, "diagram": 'flowchart LR\n  A --> B\n  click A href "http://example.test"'})
    d = run(client, auth, repo, "architect", "Explain the design", mode="design").json()
    assert d["notes"]["design"]["diagram"] == "" and any("diagram" in why for why in d["notes"]["reasons"])
    texts = {"a.py": "x = 1\n"}
    odd = {"hld": 5, "components": "no", "flows": [3, {"name": "f", "steps": "s", "files": 7}], "not_covered": {}}
    design, why = svc.verify_design(odd, texts)
    assert design["components"] == [] and design["flows"] == [] and design["not_covered"] == [] and len(why) == 1
    assert svc._clean_diagram("graph TD\n A --> B <--> C").startswith("graph TD")
    assert svc._clean_diagram("sequenceDiagram\n A->>B: hi") == ""  # only flowcharts are shown
    assert "<br/>" in svc._clean_diagram("mermaid\nflowchart TD\n  A[main.py<br/>routes] --> B")  # a line break is fine
    assert svc._diagram("graph LR\n A --> B\n click A call f()")[1].startswith("it contained 'click")
    assert svc._diagram("graph LR\n A[<script>x</script>] --> B")[0] == ""
    labelled, _ = svc.verify_design({"hld": "markdown\n## Purpose", "lld": "md\ntext"}, texts)
    assert labelled["hld"] == "## Purpose" and labelled["lld"] == "text"  # a language name on its own line is dropped


def test_the_main_files_come_entry_points_first_without_tests_or_docs_and_long_files_are_outlined(
    client, auth, tmp_path, model, session
):
    p = tmp_path / "svc"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    big = "\n".join(f"def f{i}():\n    return {i}" for i in range(200))
    files = {
        "app/auth.py": AUTH,
        "app/main.py": "from app.auth import find_user\n\n\ndef run():\n    return find_user(None, 'a')\n",
        "app/big.py": big,
        "tests/test_auth.py": "def test_x():\n    assert True\n",
        "docs/guide.md": "# guide\n",
        "package.json": "{}\n",
    }
    commit(p, files, "feat: svc", days_ago(2))
    repo = add_repo(client, auth, p, name="svc")
    found = svc.overview_files(session, session.get(Repository, repo["id"]), set())
    assert next(iter(found)) == "app/main.py"  # the entry point comes first
    assert set(found) == {"app/main.py", "app/auth.py", "app/big.py"}  # no tests, docs or package.json
    assert svc.overview_files(session, session.get(Repository, repo["id"]), {"app/main.py"}, limit=1).keys() != {
        "app/main.py"
    }
    session.commit()  # reading a file touches the repository row; end that transaction before the API updates the same row
    got = run(client, auth, repo, "architect", "zebra giraffe telescope orchestration for the moon base", mode="design")
    assert got.status_code == 201, got.text
    notes = got.json()["notes"]
    assert not any(f.startswith(("tests/", "docs/")) or f.endswith(".json") for f in notes["main_files"])
    assert "app/main.py" in notes["main_files"] and "app/big.py" in {c["file"] for c in notes["coverage"]}
    text = svc.outline(big)
    assert "a long file" in text and "def f0" in text and "def f199" not in text
    assert svc.outline("a = 1\n") == "    1  a = 1"


def test_a_model_that_spends_its_whole_budget_thinking_is_asked_again_with_more_room(
    client, auth, repo, model, monkeypatch
):
    seen = []
    real = llm._anthropic_call

    def thinking(system, prompt, max_tokens, cfg=None):
        seen.append(max_tokens)
        if len(seen) == 1:
            return LLMResult("", 100, max_tokens, 0, truncated=True)  # all of it went on thinking: no answer at all
        return real(system, prompt, max_tokens, cfg)

    monkeypatch.setattr(llm, "_anthropic_call", thinking)
    r = run(client, auth, repo)
    assert r.status_code == 201 and r.json()["status"] == "done" and r.json()["findings"]
    assert seen == [6000, 16000]  # the same call again, with room (never more than 16,000)


def test_a_reply_that_is_empty_for_another_reason_is_not_asked_again(client, auth, repo, model, monkeypatch):
    seen = []

    def empty(system, prompt, max_tokens, cfg=None):
        seen.append(max_tokens)
        return LLMResult("", 100, 0, 0, truncated=False)

    monkeypatch.setattr(llm, "_anthropic_call", empty)
    r = run(client, auth, repo)
    assert r.status_code == 409 and "could not write the review" in r.json()["detail"]
    assert seen == [6000]
