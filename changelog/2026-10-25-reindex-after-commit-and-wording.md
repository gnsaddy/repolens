# Fix a deadlock when approving a wiki edit; clearer wording; version in the sidebar

### Fixed
- Approving a wiki edit or a runbook started re-indexing before the change was committed. The indexer uses its own database session, so on PostgreSQL with inline jobs it waited forever for a row lock the request still held (the CI "Tests (PostgreSQL + pgvector)" job ran for hours), and in real use a background indexer could read the page as it was before the edit. Indexing is now scheduled after the commit. A regression test checks the order (commit, then schedule).

### Changed
- The version (v0.1-beta) is shown at the bottom of the sidebar instead of the top.
- The README leads with the DeepMind Wiki, then investigations. "Fully offline" is replaced by what is true: no AI service is called and no model is required for search, ranking or git-history analysis; integrations still need the network.
