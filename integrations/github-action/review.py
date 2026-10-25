#!/usr/bin/env python3
"""Post (or update) a RepoLens review comment on a pull request. Standard library only.

Environment: REPOLENS_URL, REPOLENS_TOKEN (a read access token), GITHUB_TOKEN, GITHUB_EVENT_PATH, GITHUB_REPOSITORY,
and optionally REPOLENS_REPO (name or id in RepoLens, default owner/name), REPOLENS_FAIL_ON (high | critical),
REPOLENS_FULL (true to include other repositories' dependents and documents: only for private repositories)."""

import json
import os
import sys
import urllib.error
import urllib.request

LEVELS = ["low", "medium", "high", "critical"]
MARKER = "<!-- repolens-pr-report -->"


def call(method: str, url: str, token: str, body: dict | None = None, accept: str = "application/json"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", accept)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def changed_files(api: str, repo: str, number: int, token: str) -> list[dict]:
    out: list[dict] = []
    for page in range(1, 11):  # up to 1,000 files
        rows = call("GET", f"{api}/repos/{repo}/pulls/{number}/files?per_page=100&page={page}", token, accept="application/vnd.github+json")
        out += [{"path": f["filename"], "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)} for f in rows]
        if len(rows) < 100:
            break
    return out


def sticky_comment(api: str, repo: str, number: int, token: str, markdown: str) -> str:
    comments = call("GET", f"{api}/repos/{repo}/issues/{number}/comments?per_page=100", token, accept="application/vnd.github+json") or []
    mine = next((c for c in comments if MARKER in (c.get("body") or "")), None)
    if mine:
        call("PATCH", f"{api}/repos/{repo}/issues/comments/{mine['id']}", token, {"body": markdown}, "application/vnd.github+json")
        return "updated"
    call("POST", f"{api}/repos/{repo}/issues/{number}/comments", token, {"body": markdown}, "application/vnd.github+json")
    return "posted"


def main(env: dict[str, str] | None = None) -> int:
    e = env if env is not None else dict(os.environ)
    event = json.load(open(e["GITHUB_EVENT_PATH"]))
    pr = event.get("pull_request")
    if not pr:
        print("This step runs on pull_request events only; nothing to do.")
        return 0
    if not e.get("REPOLENS_TOKEN") or not e.get("REPOLENS_URL"):  # pull requests from forks do not receive repository secrets
        print("No RepoLens address or token available (a pull request from a fork has no secrets); skipping the review.")
        return 0
    repo, number = e["GITHUB_REPOSITORY"], pr["number"]
    api = e.get("GITHUB_API_URL", "https://api.github.com")
    server = e["REPOLENS_URL"].rstrip("/")
    files = changed_files(api, repo, number, e["GITHUB_TOKEN"])
    if not files:
        print("No changed files.")
        return 0
    body = {"repo": e.get("REPOLENS_REPO") or repo, "files": files, "title": pr.get("title", ""), "pr_number": number,
            "app_url": server, "full": e.get("REPOLENS_FULL", "").lower() == "true"}
    try:
        report = call("POST", f"{server}/api/risk/pr-report", e["REPOLENS_TOKEN"], body)
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:300]
        print(f"RepoLens answered {err.code}: {detail}", file=sys.stderr)
        return 0 if err.code in (404, 401, 403) and not e.get("REPOLENS_STRICT") else 1  # a missing setup must not block merges
    print(f"{sticky_comment(api, repo, number, e['GITHUB_TOKEN'], report['markdown'])} the RepoLens comment: {report['level']} ({round(report['score'])}/100)")
    gate = e.get("REPOLENS_FAIL_ON", "").lower()
    if gate in LEVELS and LEVELS.index(report["level"]) >= LEVELS.index(gate):
        print(f"Failing because the risk level is {report['level']} (limit: {gate}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
