# RepoLens on pull requests (GitHub Action)

A RepoLens comment on every pull request: how risky the change is and why, who knows the code, which tests cover it, what else uses
it, and which documentation describes it (and may now be out of date). It is computed from **your own RepoLens server**; GitHub only
receives the finished comment. The comment is updated in place as the pull request changes.

```
### RepoLens review: Medium risk (42/100)

**Why**
- Dependency centrality: 6 places call changed code, 2 in other modules
- Test coverage gap: the changed function has no test that exercises it
- Historical defects: 3 of the last 20 changes to this file were fixes

**Who knows this code:** Ana (62% of recent work in these files), Ben (21% …)
**Tests that cover it:** `tests/test_user_profile.py`
**Used by 6 other places:** `handle_profile_request`, `UserRepository.find_by_id` and 4 more
**Documentation that describes this code (may need an update):** `Deleted users`, `Architecture`
```

## Set it up (five minutes)

1. Add the repository to RepoLens and let it finish indexing.
2. In RepoLens open **Settings → MCP & tokens**, create a **read** token, and copy it.
3. In the GitHub repository add a **secret** `REPOLENS_TOKEN` (that token) and a **variable** `REPOLENS_URL` (your server's address).
4. Copy `examples/workflows/repolens-review.yml` to `.github/workflows/` in that repository.

The server must be reachable from GitHub's runners (a public address, or a self-hosted runner inside your network).

## What is sent, and what is posted

- To RepoLens: the names of the changed files with their added and deleted line counts, the pull request title and number. No code,
  no diff.
- To GitHub: the comment. Because a comment on a **public** repository is public, it only mentions things from that repository
  (tests, documentation and dependents in other repositories are left out) unless you set `full: true`, which you should do only for
  private repositories. People named are the repository's own commit authors.
- The token can search and read, nothing more; revoke it in RepoLens any time. Pull requests from forks get no secrets from GitHub,
  so the step skips them.

## Options

| Input | Meaning |
|---|---|
| `url`, `token` | Your server and read token (required). |
| `repo` | The repository's name or id in RepoLens, if it differs from the GitHub `owner/name`. |
| `fail-on` | `high` or `critical`: fail the job at that level or above, so it can block a merge. Empty never fails. |
| `full` | Include dependents and documents from other repositories. Private repositories only. |

A missing setup (the repository is not in RepoLens, a bad token) never blocks a merge: the step explains and passes, unless you set
`REPOLENS_STRICT=1`.

The same report is available to any script: `POST /api/risk/pr-report` with `{repo, files: [{path, additions, deletions}], title}`.
