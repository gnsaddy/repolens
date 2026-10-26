# Route matching no longer lets one file crowd out the right route

### Fixed
- "How does the user endpoint assign roles?" returned the three SCIM `/Users` routes and never the main `POST /api/auth/users` handler or its permission check. Five routes tied on score and only three fit, so the tie went to whichever file was indexed first.
- Matching routes are now ordered by score and then by how often the handler uses the question's words, and the top routes are taken from different files first (at most one per file), then filled by score. On a real index of this repository the saved quality check "finds api/auth.py and services/policy.py" now passes.

### Changed
- The answer-memory `PIPELINE` moves from `p2` to `p3`, so answers saved before this change are not replayed.

### Upgrade notes
- None. No re-index needed.
