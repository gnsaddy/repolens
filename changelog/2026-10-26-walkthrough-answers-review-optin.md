# Walkthrough answers for how/why questions, and the independent review becomes opt-in

### Changed
- **Independent review of investigations is now off by default** and switched by an admin in Settings (`PUT /api/settings/review`, audited, migration 0023, column `llm_settings.independent_review`). When on, its findings are shown in the report as before. **It no longer changes the confidence or the ranking** (the automatic 80% penalty, the "contested" flag and re-ranking are removed); whether it agreed is still recorded in `verification.review`. The checklist and red flags stay on.
- **How/why/explain questions** (Ask chat and the Knowledge page's Ask box): matched routes from the index, their handlers and the functions the handlers call are added as sources, and the model is asked for a structured walkthrough (summary, ordered steps each cited, "Not covered by your sources") with up to 2,500 output tokens. "How many/much/often…" lookups and other questions keep the short prompt. New `services/walkthrough.py`.
- The Knowledge page's Ask box now reports a failed model call (reason, model, "AI unavailable" badge, mode `failed`) the way the chat does, instead of silently showing a few passages. Shared `describe_failure` moved to `services/llm.py`.

### Why
- "Check the user endpoint, how user will assign the roles?" returned one line: the route and its permission check were never retrieved (route decorators sit outside the indexed function chunks) and the prompt demanded brevity.
