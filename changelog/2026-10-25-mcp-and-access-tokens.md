# MCP server and personal access tokens

### Added
- **MCP server** at `/api/mcp` (Model Context Protocol over HTTP, JSON-RPC 2.0): an editor or agent such as Claude Code or Cursor can `search` code, documents and pull requests, `ask` with cited sources, `find_symbol` and `symbol_brief` before changing code, `read_wiki_page`, `who_to_ask`, `change_risk`, `get_investigation`, and (with a write token) `investigate`. Tools run as the token's owner, so repository restrictions, roles and audit apply unchanged; the audit log records which tool was called, not what was asked. `python -m repolens.devtools.mcp_stdio` bridges clients that only speak standard input/output.
- **Access tokens** (Profile): named, expiring, revocable tokens (`rlp_…`) for scripts, CI and editors. A *read* token is a viewer; a *write* token is at most a member and never an admin. They are shown once and stored as hashes, cannot create other tokens, and cannot change a password, sessions, profile or authenticator.
- Copy-paste setup for Claude Code, Cursor and Claude Desktop on the Profile page.

### Upgrade notes
- Migration 0019 adds `api_tokens` (development databases add it on start).
