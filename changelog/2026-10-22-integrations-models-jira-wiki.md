# Integrations, multi-model routing, Jira analysis and wiki tools

### Added
- Connector catalogue in Settings (GitHub, Bitbucket, Jira, Confluence and more) with browse screens for repositories, pull requests, issues, spaces and pages.
- Several language models and providers (Anthropic, Gemini, OpenAI-compatible) with tiers, savings mode, fallback and escalation; a guided card that tests a key before offering models.
- Answer memory: repeated or re-worded questions are answered locally, with an "Asked before" list shown first.
- Jira: read tickets and comments in the app, analyse them, investigate with the distilled signals, publish approved reports to any issue.
- Sessions, a profile page and an Insights page.
- DeepMind Wiki: search by meaning, in-place editing, a per-repository code atlas, and an at-a-glance card for each page.
- Graph: a "Before you change this" brief for a selected symbol.
- Agents share a digest of an investigation instead of re-reading all the evidence.

### Changed
- Retrieved context is condensed and de-duplicated before it reaches a model; a cheap model's uncited answer is retried on a stronger one.

### Fixed
- Bitbucket Atlassian tokens, workspace listing, clearer 401/403/404 messages; provider errors no longer echo API keys.
