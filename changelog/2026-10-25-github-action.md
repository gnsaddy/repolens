# GitHub Action: a RepoLens review on every pull request

### Added
- `integrations/github-action`: a composite Action that comments on pull requests with change risk and its reasons, who knows the code, the tests that cover it, what else uses it, and the documentation that describes it. The comment updates in place; `fail-on: high` can gate merges. A workflow example is in `examples/workflows/repolens-review.yml` and the guide in `docs/github-action.md`.
- `POST /api/risk/pr-report`: the same report for any script. It takes only file names and line counts, stores nothing, and works with a read token. The repository can be named by id, name or its GitHub `owner/name`.

### Security
- The comment mentions only the pull request's own repository unless `full` is set, because comments on public repositories are public. Pull requests from forks (which get no secrets) are skipped, and a missing setup never blocks a merge.
