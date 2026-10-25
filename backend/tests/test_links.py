import pytest

from repolens.config import get_settings
from repolens.models import GithubConnection, Repository
from repolens.security import encrypt_secret
from repolens.services import gitops, indexer
from repolens.services.jira import markdown_to_adf
from repolens.services.links import (
    basic_auth_header,
    commit_url,
    file_url,
    has_embedded_credentials,
    pr_url,
    strip_credentials,
    web_base,
)
from tests.demo_repo import BUG_REPORT
from tests.test_api import add_repo


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/acme/shop.git", "https://github.com/acme/shop"),
        ("git@github.com:acme/shop.git", "https://github.com/acme/shop"),
        ("ssh://git@gitlab.example.com/team/sub/app.git", "https://gitlab.example.com/team/sub/app"),
        (
            "https://user:ghp_secrettoken@github.com/acme/shop.git",
            "https://github.com/acme/shop",
        ),  # creds never survive
        ("https://ghe.corp.io:8443/o/r", "https://ghe.corp.io:8443/o/r"),
        ("", ""),
        ("/local/path/repo", ""),
        ("file:///x/y", ""),
        ("https://github.com/onlyowner", ""),
        ("https://host/../x/y", ""),
    ],
)
def test_web_base_normalises_and_never_leaks_credentials(raw, expected):
    assert web_base(raw) == expected
    assert "ghp_secrettoken" not in web_base(raw)


def test_link_formats_for_github_and_gitlab():
    gh, gl = "https://github.com/acme/shop", "https://gitlab.com/acme/shop"
    sha = "a" * 40
    assert commit_url(gh, sha) == f"{gh}/commit/{sha}" and commit_url(gl, sha) == f"{gl}/-/commit/{sha}"
    assert file_url(gh, sha, "src/a b.py", 9, 11) == f"{gh}/blob/{sha}/src/a%20b.py#L9-L11"
    assert file_url(gl, sha, "src/a.py", 9, 11) == f"{gl}/-/blob/{sha}/src/a.py#L9-11"
    assert file_url(gh, "docs-123", "a.md") == f"{gh}/blob/HEAD/a.md"  # non-git revisions fall back to HEAD
    assert pr_url(gh, 7) == f"{gh}/pull/7" and pr_url(gl, 7) == f"{gl}/-/merge_requests/7"
    assert commit_url("", sha) == "" and file_url(gh, sha, "") == ""


def test_credential_helpers():
    assert has_embedded_credentials("https://u:p@github.com/a/b") and not has_embedded_credentials(
        "git@github.com:a/b.git"
    )
    assert strip_credentials("https://u:p@github.com:444/a/b") == "https://github.com:444/a/b"
    assert strip_credentials("git@github.com:a/b.git") == "git@github.com:a/b.git"


def test_repository_urls_with_credentials_are_rejected(client, auth):
    r = client.post(
        "/api/repositories",
        headers=auth,
        json={"name": "x", "source": "git", "url": "https://u:tok@github.com/a/b.git"},
    )
    assert r.status_code == 400 and "credentials" in r.text


def test_legacy_credential_urls_are_scrubbed_in_responses(client, auth, session):
    from repolens.models import Organization

    org = session.query(Organization).one()
    session.add(Repository(org_id=org.id, name="legacy", source="git", url="https://u:ghp_leaked@github.com/a/b.git"))
    session.commit()
    body = client.get("/api/repositories", headers=auth).text
    assert "ghp_leaked" not in body and "github.com/a/b.git" in body


def test_evidence_report_and_search_carry_working_links(client, auth, demo_repo):
    path, bad_sha = demo_repo
    gitops._git(str(path), "remote", "add", "origin", "https://github.com/acme/user-service.git")
    repo = add_repo(client, auth, path)
    assert (
        client.get(f"/api/repositories/{repo['id']}", headers=auth).json()["web_url"]
        == "https://github.com/acme/user-service"
    )
    inv = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [repo["id"]]}).json()
    inv = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    ev = {e["type"]: e for e in inv["hypotheses"][0]["evidence"]}
    assert ev["commit"]["url"] == f"https://github.com/acme/user-service/commit/{bad_sha}"
    assert (
        ev["code"]["url"].startswith("https://github.com/acme/user-service/blob/")
        and "src/user_profile.py#L" in ev["code"]["url"]
    )
    assert f"](https://github.com/acme/user-service/commit/{bad_sha})" in inv["report_md"]
    hits = client.post("/api/knowledge/search", headers=auth, json={"query": "get profile metadata"}).json()
    assert hits[0]["url"].startswith("https://github.com/acme/user-service/blob/")
    ask = client.post("/api/knowledge/ask", headers=auth, json={"query": "How are deleted users handled?"}).json()
    assert all("url" in s for s in ask["sources"])


def test_repos_without_a_remote_have_no_links_instead_of_broken_ones(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])  # no origin remote
    assert client.get(f"/api/repositories/{repo['id']}", headers=auth).json()["web_url"] == ""
    inv = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [repo["id"]]}).json()
    inv = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    assert all("url" not in e for e in inv["hypotheses"][0]["evidence"] if e["type"] in ("code", "commit"))
    assert "](http" not in inv["report_md"]


def test_adf_links_become_marks_and_unsafe_schemes_are_not_linked():
    adf = markdown_to_adf(
        "- Commit [`abc123`](https://github.com/a/b/commit/abc123) by dev\nSee [x](javascript:alert(1))"
    )
    para = adf["content"][0]["content"]
    linked = [n for n in para if n.get("marks")]
    assert (
        linked[0]["text"] == "abc123"
        and linked[0]["marks"][0]["attrs"]["href"] == "https://github.com/a/b/commit/abc123"
    )
    flat = str(adf["content"][1])
    assert "marks" not in flat and "javascript:alert" in flat  # shown as text, never a clickable link


# ------------------------------------------------------------------ private clones use the connected token safely
def test_clone_uses_token_header_only_for_the_matching_host(session, monkeypatch):
    from repolens.models import Organization

    org = Organization(name="o")
    session.add(org)
    session.commit()
    session.add(
        GithubConnection(
            org_id=org.id, token_encrypted=encrypt_secret("ghp_clone_token"), api_base="https://api.github.com"
        )
    )
    session.commit()
    seen = {}
    monkeypatch.setattr(gitops, "is_repo", lambda p: False)
    monkeypatch.setattr(
        gitops, "clone", lambda url, dest, auth_header=None, **kw: seen.update(url=url, auth=auth_header)
    )
    monkeypatch.setattr(get_settings(), "repos_dir", "/tmp/rl-clone-test", raising=False)
    gh = Repository(org_id=org.id, name="r", source="git", url="https://github.com/acme/private.git")
    other = Repository(org_id=org.id, name="r2", source="git", url="https://evil.example.com/acme/private.git")
    session.add_all([gh, other])
    session.commit()
    indexer.resolve_checkout(gh, session)
    assert seen["url"] == "https://github.com/acme/private.git" and "ghp_clone_token" not in seen["url"]
    assert seen["auth"] == basic_auth_header("ghp_clone_token")
    indexer.resolve_checkout(other, session)
    assert seen["auth"] is None  # token is never sent to a host it was not issued for
    gh.local_path = other.local_path = ""
