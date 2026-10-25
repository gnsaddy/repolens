# Safeguards for public repositories

### Added
- Suggested fixes show whether the target GitHub repository is public (`target_public` on the fix, a "public repository" badge in the app).
- Approving a fix for a public repository shows a prominent warning and needs a ticked acknowledgement; the app then sends `confirm_public: true`.
- The pull request comment has a strict mode for public repositories (`public` flag on `POST /api/risk/pr-report`).

### Changed
- The decision endpoint answers `409` for a public target unless `confirm_public` is true. Private repositories and rejecting are unchanged.
- The GitHub Action reads the event's repository visibility and sends `public: true` unless the repository is private; `full` is ignored on public repositories.
- The confirmation dialog can carry a warning and a required acknowledgement.

### Security
- On public repositories the comment contains only the score, risk-signal names and file paths from the pull request itself: no ticket text, no names of people, no documents or dependents. The server enforces it.

### Upgrade notes
- API clients that approve fixes for public repositories must now send `confirm_public: true`.
- Update the Action together with the server; an older Action does not send `public`, so the server gives the fuller comment.
