"""The dependency reviewer and the documentation checker read their own kind of file, whatever the request names."""

import json

import pytest

from repolens.config import get_settings
from repolens.services import llm
from repolens.services import personas as svc
from repolens.services.llm import LLMResult
from tests.test_api import add_repo
from tests.test_groups import days_ago
from tests.test_personas import AUTH, REQUEST, run
from tests.test_risk import commit, git

PACKAGE = '{\n  "name": "shop",\n  "dependencies": {\n    "left-pad": "*",\n    "express": "^4.0.0"\n  }\n}\n'
DOCKERFILE = "FROM python:latest\nRUN pip install -r requirements.txt\n"
README = "# shop\n\nStart it with `make serve`, then open the page.\n"


@pytest.fixture
def seen(monkeypatch):
    """A fake model that records the prompt it was given and answers from `state.reply`."""
    monkeypatch.setenv("REPOLENS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    state = type("S", (), {"prompts": [], "reply": {"summary": "ok", "findings": []}})()

    def fake(system, prompt, max_tokens, cfg=None):
        state.prompts.append(prompt)
        return LLMResult(json.dumps(state.reply), 900, 300)

    monkeypatch.setattr(llm, "_anthropic_call", fake)
    yield state
    get_settings.cache_clear()


@pytest.fixture
def project(client, auth, tmp_path):
    p = tmp_path / "shop"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    files = {"app/auth.py": AUTH, "package.json": PACKAGE, "Dockerfile": DOCKERFILE, "README.md": README}
    files |= {"tests/package.json": '{"dependencies": {"x": "*"}}', "docs/guide.md": "Set `SHOP_MODE` first.\n"}
    commit(p, files, "feat: shop", days_ago(3))
    return add_repo(client, auth, p, name="shop")


def test_the_two_new_personas_are_listed_without_a_design_overview(client, auth):
    got = {p["key"]: p for p in client.get("/api/personas", headers=auth).json()["personas"]}
    assert {"dependency-reviewer", "docs-reviewer"} <= set(got)
    assert got["dependency-reviewer"]["modes"] == ["review"] and got["docs-reviewer"]["modes"] == ["review"]
    assert "advisories" in got["dependency-reviewer"]["summary"]


def test_the_dependency_reviewer_is_given_the_dependency_files_even_when_the_request_names_none(
    client, auth, project, seen
):
    seen.reply = {
        "summary": "Two loose versions and a moving tag.",
        "findings": [
            {
                "title": "Any version of left-pad",
                "severity": "medium",
                "file": "package.json",
                "evidence": '"left-pad": "*"',
                "explanation": "A build can pick a new major version by itself.",
                "suggestion": "Pin it.",
            },
            {
                "title": "Moving base image",
                "severity": "low",
                "file": "Dockerfile",
                "evidence": "FROM python:latest",
                "explanation": "latest changes under you.",
                "suggestion": "Use a version tag.",
            },
            {"title": "Invented", "severity": "high", "file": "package.json", "evidence": '"lodash": "1.0.0"'},
        ],
    }
    r = run(client, auth, project, persona="dependency-reviewer", request=REQUEST)
    assert r.status_code == 201, r.text
    prompt = seen.prompts[0]
    assert "FILE package.json" in prompt and "FILE Dockerfile" in prompt
    assert "tests/package.json" not in prompt  # a test fixture is not a project dependency file
    body = r.json()
    assert [f["file"] for f in body["findings"]] == ["package.json", "Dockerfile"]  # the invented one is dropped
    assert body["notes"]["standing_files"] == ["Dockerfile", "package.json"] and body["notes"]["dropped"] == 1


def test_the_documentation_checker_reads_the_readme_and_docs_against_the_code(client, auth, project, seen):
    seen.reply = {
        "summary": "The README names a command the code does not have.",
        "findings": [
            {
                "title": "make serve does not exist",
                "severity": "medium",
                "file": "README.md",
                "evidence": "Start it with `make serve`, then open the page.",
                "explanation": "No Makefile is shown.",
                "suggestion": "Name the real command.",
            }
        ],
    }
    r = run(client, auth, project, persona="docs-reviewer", request=REQUEST)
    assert r.status_code == 201, r.text
    assert "FILE README.md" in seen.prompts[0] and "FILE docs/guide.md" in seen.prompts[0]
    assert "FILE app/auth.py" in seen.prompts[0]  # the code the request names is read too
    assert [f["file"] for f in r.json()["findings"]] == ["README.md"]
    assert r.json()["notes"]["standing_files"] == ["README.md", "docs/guide.md"]


def test_a_long_dependency_file_is_read_whole_and_a_very_long_one_says_where_it_was_cut(client, auth, tmp_path, seen):
    p = tmp_path / "big"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    lines = [f"package{i}==1.{i}.0" for i in range(1, 500)]
    lines[149] = "leftpad>=0"
    commit(p, {"app/auth.py": AUTH, "requirements.txt": "\n".join(lines) + "\n"}, "feat: deps", days_ago(3))
    repo = add_repo(client, auth, p, name="big")
    r = run(client, auth, repo, persona="dependency-reviewer", request=REQUEST)
    assert r.status_code == 201, r.text
    prompt = seen.prompts[0]
    assert "leftpad>=0" in prompt  # line 150 is shown, not lost in an outline
    assert "cut: only the first 400 of 499 lines are shown" in prompt and "package450==" not in prompt
    cov = {c["file"]: c for c in r.json()["notes"]["coverage"]}
    assert cov["requirements.txt"].get("cut") is True and "outline" not in cov["requirements.txt"]


def test_a_persona_without_standing_files_does_not_get_any(client, auth, project, seen):
    r = run(client, auth, project, persona="security-auditor", request=REQUEST)
    assert r.status_code == 201 and r.json()["notes"]["standing_files"] == []
    assert "FILE package.json" not in seen.prompts[0]


def test_the_standing_files_are_the_shallowest_and_few(session, client, auth, project):
    from repolens.models import Repository

    repo = session.query(Repository).filter_by(id=project["id"]).one()
    got = svc.always_files(session, repo, svc.MANIFESTS, set())
    assert list(got) == ["Dockerfile", "package.json"]  # a test fixture's file is not the project's own
    assert svc.always_files(session, repo, svc.MANIFESTS, {"package.json"}).keys() == {"Dockerfile"}
    assert len(svc.always_files(session, repo, svc.MANIFESTS, set(), limit=1)) == 1
