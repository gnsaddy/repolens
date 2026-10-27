"""Ask on the Graph page: answered from the chosen repositories and the dependency analysis, and nothing else."""

import subprocess

import pytest

from repolens.models import Repository
from repolens.services.llm import LLMClient, LLMResult
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_groups import days_ago, libs, make_shop, make_userlib  # noqa: F401  (fixtures)
from tests.test_risk import commit, git


def ids(*repos):
    return [r["id"] for r in repos]


@pytest.fixture
def model(monkeypatch):
    """A model that records what it was shown and answers with a citation."""
    seen: list[dict] = []

    def complete(this, agent, system, prompt, **k):
        seen.append({"system": system, "prompt": prompt})
        return LLMResult("The shop calls get_user from the user library [S1].", 30, 20)

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", complete)
    return seen


def ask(client, h, repos, question="How does the shop depend on the user library?", **body):
    return client.post("/api/groups/ask", headers=h, json={"repo_ids": ids(*repos), "question": question, **body})


def make_other(tmp_path):
    p = tmp_path / "other-svc"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(p, {"other/zebra.py": "def zebrafish_migration():\n    return 'ZEBRAFISH'\n"}, "feat: other", days_ago(2))
    return p


def test_the_model_is_shown_the_dependency_analysis_with_its_proof(client, auth, libs, model):  # noqa: F811
    r = ask(client, auth, libs)
    assert r.status_code == 200, r.text
    prompt = model[0]["prompt"]
    assert "How these repositories fit together" in prompt and "shop-app depends on users-lib" in prompt
    assert "from userlib import get_user, UserClient" in prompt  # the import line is a source, so it can be cited
    assert "dependency analysis" in model[0]["system"]
    out = r.json()
    assert out["mode"] == "llm" and any(s["kind"] == "analysis" for s in out["sources"])


def test_only_the_chosen_repositories_are_searched(client, auth, libs, tmp_path, model):  # noqa: F811
    add_repo(client, auth, make_other(tmp_path), name="other-svc")
    r = ask(client, auth, libs, "Where is zebrafish_migration and what is ZEBRAFISH?")
    assert "other/zebra.py" not in model[0]["prompt"] and "'ZEBRAFISH'" not in model[0]["prompt"]
    assert not any("other" in s["file"] for s in r.json()["sources"])


def test_a_link_can_be_explained_without_typing_a_question(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    r = ask(client, auth, libs, "", src=shop["id"], dst=lib["id"])
    assert r.status_code == 200, r.text
    assert "Explain how shop-app depends on users-lib" in r.json()["query"]
    assert "The link from shop-app to users-lib" in model[0]["prompt"]
    back = ask(client, auth, libs, "", src=lib["id"], dst=shop["id"])
    assert back.status_code == 200 and "The link from users-lib to shop-app" not in model[1]["prompt"]


def test_what_depends_on_a_node_is_added_as_a_source(client, auth, libs, model):  # noqa: F811
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": ",".join(ids(*libs))}).json()
    node = next(n for n in g["nodes"] if n["name"] == "get_user")
    r = ask(client, auth, libs, "", node_id=node["id"])
    assert r.status_code == 200 and r.json()["query"] == "What depends on get_user, and what would break if it changed?"
    assert "What depends on get_user" in model[0]["prompt"] and "shop/checkout.py:checkout" in model[0]["prompt"]


def test_a_link_between_repositories_that_were_not_chosen_is_refused(client, auth, libs, tmp_path, model):  # noqa: F811
    shop, lib = libs
    other = add_repo(client, auth, make_other(tmp_path), name="other-svc")
    assert ask(client, auth, [shop, lib], "", src=shop["id"], dst=other["id"]).status_code == 404
    assert ask(client, auth, [shop, lib], "", src=shop["id"]).status_code == 422  # a half-given link is no question


def test_a_repository_someone_cannot_open_never_reaches_the_answer(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(f"/api/repositories/{lib['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []})
    assert ask(client, ann, libs).status_code == 404  # asking about it by id is the same as it not existing
    only_shop = ask(client, ann, [shop], "What does the shop import?")
    assert only_shop.status_code == 200 and "users-lib" not in model[0]["prompt"]


def test_a_node_in_a_repository_someone_cannot_open_is_not_found(client, auth, libs, model):  # noqa: F811
    shop, lib = libs
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": ",".join(ids(*libs))}).json()
    node = next(n for n in g["nodes"] if n["name"] == "get_user")
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(f"/api/repositories/{lib['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []})
    assert ask(client, ann, [shop], "", node_id=node["id"]).status_code == 404


def test_viewers_may_ask_and_an_empty_question_is_refused(client, auth, libs, model):  # noqa: F811
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    assert ask(client, viewer, libs).status_code == 200
    assert ask(client, auth, libs, "  ").status_code == 422


def test_a_failed_model_call_is_reported_not_replaced_by_the_facts(client, auth, libs, monkeypatch):  # noqa: F811
    def boom(this, *a, **k):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", boom)
    out = ask(client, auth, libs).json()
    assert out["mode"] == "failed" and "could not answer" in out["answer"].lower()


def test_with_no_model_the_answer_is_labelled_as_plain_passages(client, auth, libs):  # noqa: F811
    out = ask(client, auth, libs).json()
    assert out["mode"] == "extractive" and "extracted, not generated" in out["answer"]


def test_a_saved_answer_is_not_reused_once_the_dependencies_changed(client, auth, libs, model, session):  # noqa: F811
    shop, lib = libs
    ask(client, auth, libs)
    ask(client, auth, libs)
    assert len(model) == 1  # the second identical question is answered from memory
    path = session.get(Repository, shop["id"]).url
    with open(f"{path}/shop/reports.py", "w") as f:
        f.write("from userlib import unused_helper\n\n\ndef report():\n    return unused_helper()\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    cmd = ["git", "-C", str(path), "-c", "user.name=d", "-c", "user.email=d@x.io", "commit", "-qm", "use helper"]
    subprocess.run(cmd, check=True)
    assert client.post(f"/api/repositories/{shop['id']}/index", headers=auth).status_code == 202
    ask(client, auth, libs)
    assert len(model) == 2 and "shop/reports.py:report" in model[1]["prompt"]


def test_a_saved_answer_key_never_outgrows_its_column(client, auth, libs, model, session):  # noqa: F811
    """PostgreSQL refuses a key longer than the column (SQLite does not), and the failure was blamed on the model."""
    from repolens.models import AnswerMemory

    shop, lib = libs
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": ",".join(ids(*libs))}).json()
    node = next(n for n in g["nodes"] if n["name"] == "get_user")
    for body in ({"src": shop["id"], "dst": lib["id"]}, {"node_id": node["id"]}, {}):
        out = ask(client, auth, libs, "" if body else "How does the shop depend on the user library?", **body).json()
        assert out["mode"] == "llm", out["answer"]
    keys = [k for (k,) in session.query(AnswerMemory.scope_key)]
    assert keys and max(len(k) for k in keys) <= 64


def make_docs(tmp_path):
    p = tmp_path / "docsy-svc"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    files = {
        "docsy/handlers.py": "def render_page(user_id):\n    return get_user(user_id)\n",
        "docs/en/tutorial.md": "# Tutorial\n\nThis tutorial shows how render_page asks the user library for a user.\n",
        "docs/de/tutorial.md": "# Anleitung\n\nZusatzinformation: diese Anleitung zeigt render_page und get_user.\n",
        "docs/ja/tutorial.md": "# チュートリアル\n\nZusatzinformation render_page get_user の使い方。\n",
    }
    commit(p, files, "feat: docs", days_ago(2))
    return p


def test_a_dependency_question_prefers_code_and_never_quotes_translated_documents(client, auth, libs, tmp_path, model):  # noqa: F811
    docsy = add_repo(client, auth, make_docs(tmp_path), name="docsy-svc")
    r = ask(client, auth, [*libs, docsy], "How does docsy render_page use get_user from the user library?")
    assert r.status_code == 200, r.text
    assert "Zusatzinformation" not in model[0]["prompt"] and "docs/de/" not in model[0]["prompt"]
    assert not any(s["kind"] == "doc" for s in r.json()["sources"])  # no document unless the question asks for one


def test_a_question_about_documents_gets_them_but_still_not_the_translated_copies(client, auth, libs, tmp_path, model):  # noqa: F811
    docsy = add_repo(client, auth, make_docs(tmp_path), name="docsy-svc")
    r = ask(client, auth, [*libs, docsy], "What does the tutorial say about render_page?")
    files = [s["file"] for s in r.json()["sources"]]
    assert "docs/en/tutorial.md" in files
    assert not any(f.startswith(("docs/de/", "docs/ja/")) for f in files)


def test_a_question_written_in_another_language_may_use_translated_documents(client, auth, libs, tmp_path, model):  # noqa: F811
    docsy = add_repo(client, auth, make_docs(tmp_path), name="docsy-svc")
    r = ask(
        client,
        auth,
        [docsy],
        "Wie funktioniert die Anleitung für render_page? Bitte die tutorial Dokumentation erklären – ä",
    )
    assert any(s["file"].startswith("docs/") for s in r.json()["sources"])
