from repolens.agents.workflow import _is_context_file
from tests.test_api import add_repo, run_investigation


def test_docs_tests_and_changelogs_are_context_files():
    for p in ["docs/arch.md", "README.md", "backend/tests/test_x.py", "web/a.test.tsx", "changelog/x.md", "Makefile"]:
        assert _is_context_file(p), p
    for p in ["src/app/user_profile.py", "backend/repolens/services/llm.py", "web/src/api.ts"]:
        assert not _is_context_file(p), p


def test_shortlist_has_one_hypothesis_per_file(client, auth, demo_repo):
    path, _ = demo_repo
    inv = run_investigation(client, auth, add_repo(client, auth, path)["id"])
    files = [next(e["file"] for e in h["evidence"] if e["type"] == "code") for h in inv["hypotheses"]]
    assert files and len(files) == len(set(files)) and len(files) <= 5
