# Jira: new ticket list, analysis and investigation of several tickets, and "Start from Jira"

### Added
- The Jira page is a ticket list: type tag, key, summary, priority, status (coloured by category), assignee, last update, comment count and whether an RCA exists. Filters for project, **type (every type by default, no longer only Bugs)**, status, assignee (anyone, me, unassigned) and text; choose 25, 50 or 100 tickets, newest update first.
- Select up to ten tickets, then **Analyse together** (what they share: errors, files, symbols, components; likely duplicates; each ticket's proposed cause and gaps; related docs and code) or **Investigate all** (one investigation per ticket; tickets that already have one keep it).
- **Start from Jira** on the Investigations page, shown only when Jira is connected: search, read, analyse or investigate tickets without leaving the page.
- API: `GET /api/jira/issue-types`, `POST /api/jira/analyze-batch`, `POST /api/jira/investigate-batch`; `GET /api/jira/issues` takes `limit` (up to 100) and returns more fields.

### Changed
- The filter list of issue types comes from your Jira.
