# "What leaves this server" panel

### Added
- Settings → AI & search shows every service this server sends data to, built from what is connected right now: language models, an embedding or reranking service, GitHub, Bitbucket, Jira, Confluence, other wiki sources, a single sign-on provider and a trace collector. Each entry names the host, says what it receives, whether it stays on the machine (a local model server does), and where an admin manages it.
- `GET /api/settings/outbound` for the same list.

### Changed
- With nothing connected the panel reads "Nothing leaves": that is the default install.
