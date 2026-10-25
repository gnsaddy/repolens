# A fuller dashboard

### Added
- **Needs attention** at the top: repositories that failed to index, investigations that failed this week (with a link to run them again), wiki pages that may be out of date, suggested wiki edits waiting for review, finished investigations nobody has confirmed, low-confidence results, and repositories still indexing. Most serious first; each line links to where to fix it, or says "Nothing needs your attention".
- **Trends**: investigations per day for two weeks (finished and failed), AI use for 30 days with tokens saved and the share of answers people found helpful, and a knowledge-base snapshot (pages, chunks, graph, how much code the docs explain, wiki issues).
- **Recent activity** for admins: who did what, from the audit trail (noise such as ratings is left out).
- `GET /api/dashboard/insights` (refreshed every minute; the headline numbers still refresh every 10 seconds).
