import pytest

from repolens.services import golden as svc
from repolens.services.llm import LLMClient, LLMResult
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_chat import make_user


@pytest.fixture
def repo(client, auth, demo_repo):
    return add_repo(client, auth, demo_repo[0])


def add(client, h, question="Where is get_profile defined?", **kw):
    r = client.post("/api/golden/questions", headers=h, json={"question": question, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def run(client, h, mode="retrieval"):
    r = client.post("/api/golden/run", headers=h, json={"mode": mode})
    assert r.status_code == 202, r.text
    return r.json()


def test_a_retrieval_run_checks_that_the_expected_files_are_found_and_costs_nothing(client, auth, repo, monkeypatch):
    called = []
    monkeypatch.setattr(LLMClient, "complete", lambda *a, **k: called.append(1))
    good = add(client, auth, expect_files=["user_profile.py"])
    bad = add(client, auth, "How does get_profile treat a deleted user?", expect_files=["does_not_exist.py"])
    r = run(client, auth)
    assert r["status"] == "completed" and r["total"] == 2 and r["passed"] == 1 and not called
    by = {x["id"]: x for x in r["results"]}
    assert by[good]["passed"] and by[good]["checks"][0]["name"] == "finds user_profile.py"
    assert not by[bad]["passed"] and by[bad]["checks"][0]["detail"] == "not among the sources used"
    assert "user_profile.py" in " ".join(by[bad]["files"])  # what was found instead is shown


def test_a_full_run_also_checks_phrases_and_that_the_model_answered(client, auth, repo, monkeypatch):
    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", lambda this, *a, **k: LLMResult("It is in user_profile.py [S1].", 5, 5))
    a = add(client, auth, expect_files=["user_profile.py"], expect_phrases=["user_profile.py"])
    b = add(client, auth, "Where is get_profile defined and who calls it?", expect_phrases=["kubernetes"])
    r = run(client, auth, "full")
    by = {x["id"]: x for x in r["results"]}
    assert by[a]["passed"] and by[a]["answer"].startswith("It is in")
    assert not by[b]["passed"] and any(c["detail"] == "missing from the answer" for c in by[b]["checks"])


def test_a_failed_model_call_fails_the_question_instead_of_passing_it(client, auth, repo, monkeypatch):
    def boom(this, *a, **k):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", boom)
    add(client, auth, expect_phrases=["profile"])
    r = run(client, auth, "full")
    assert r["passed"] == 0 and any(c["detail"] == "the answer was 'failed'" for c in r["results"][0]["checks"])


def test_comparison_names_what_broke_and_what_was_fixed(client, auth, repo, demo_repo):
    a = add(client, auth, expect_files=["user_profile.py"])
    run(client, auth)
    first = client.get("/api/golden", headers=auth).json()
    assert first["latest"]["passed"] == 1 and first["compare"]["delta"] is None
    client.delete(f"/api/golden/questions/{a}", headers=auth)
    add(client, auth, "Where is get_profile defined?", expect_files=["nothing_here.py"])
    run(client, auth)
    second = client.get("/api/golden", headers=auth).json()
    assert second["latest"]["passed"] == 0 and len(second["runs"]) == 2
    assert second["questions"][0]["last"]["passed"] is False


def test_the_overview_says_what_changed_since_the_last_run(client, auth, repo):
    add(client, auth, expect_files=["user_profile.py"])
    run(client, auth)
    assert client.get("/api/golden", headers=auth).json()["changed"] == []
    client.post(
        "/api/skills",
        headers=auth,
        json={"title": "House rule", "body": "Always mention the file path.", "always": True},
    )
    assert client.get("/api/golden", headers=auth).json()["changed"] == ["the skills"]


def test_fingerprint_changes_name_the_component():
    assert svc.changed({"index": "a", "skills": "b"}, {"index": "a", "skills": "c"}) == ["the skills"]
    assert svc.changed({"index": "a"}, {"index": "b", "pipeline": "p2"}) == [
        "the indexed code or documents",
        "how answers are produced",
    ]


def test_limits_and_validation(client, auth, repo):
    assert client.post("/api/golden/run", headers=auth, json={}).status_code == 409  # nothing saved yet
    assert client.post("/api/golden/questions", headers=auth, json={"question": "hi"}).status_code == 422
    assert (
        client.post(
            "/api/golden/questions", headers=auth, json={"question": "A real question here", "repo_ids": ["nope"]}
        ).status_code
        == 404
    )
    got = add(client, auth, expect_files=["  a.py ", "a.py", "", "key ghp_" + "a" * 36])
    q = [x for x in client.get("/api/golden", headers=auth).json()["questions"] if x["id"] == got][0]
    assert q["expect_files"][0] == "a.py" and len(q["expect_files"]) == 2 and "ghp_" not in q["expect_files"][1]
    for i in range(svc.MAX_FULL + 1):
        add(client, auth, f"Question number {i} about profiles")
    assert client.post("/api/golden/run", headers=auth, json={"mode": "full"}).status_code == 409


def test_who_can_do_what_and_other_workspaces_see_nothing(client, auth, repo):
    m = make_user(client, auth, "m@acme.io")
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    q = add(client, auth, expect_files=["user_profile.py"])
    assert (
        client.post("/api/golden/questions", headers=viewer, json={"question": "Can a viewer add this?"}).status_code
        == 403
    )
    assert client.post("/api/golden/run", headers=viewer, json={}).status_code == 403
    assert client.delete(f"/api/golden/questions/{q}", headers=m).status_code == 403  # someone else's
    mine = add(client, m, "A member's own question here")
    assert client.delete(f"/api/golden/questions/{mine}", headers=m).status_code == 204
    r = run(client, auth)
    assert (
        client.get(f"/api/golden/runs/{r['id']}", headers=m).status_code == 404
    )  # a run shows what its runner could see
    assert client.get(f"/api/golden/runs/{r['id']}", headers=auth).status_code == 200
    other = register(client, "o@else.io", "Else")
    assert client.get("/api/golden", headers=other).json()["questions"] == []
    assert client.get(f"/api/golden/runs/{r['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/golden/questions/{q}", headers=other).status_code == 404
