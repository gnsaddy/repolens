# Gaps, "Measure it on my repo", a guided start and the fallback tooltip

### Added
- **Gaps** tab in Ask: your own unanswered questions, grouped and counted, each with **Draft a page** (a starter page saved to a documentation collection). `GET /api/chats/gaps`; built only from your private chats.
- **Measure** on a repository: runs the real-bug evaluation on its own history in a separate process and shows top 1 / 3 / 5, a 95% range, the random baseline, hits and misses per fix, and caveats. `POST/GET /api/repositories/{id}/measure`; table `eval_runs` (migration 0022); policy action `repo.measure`.
- Dashboard **Getting started** guide: six steps with a progress bar and a highlighted next step ("Ask your first question", "Measure it on your repo" added); `first_steps` on `/api/dashboard/insights` (counts only).
- **(i) tooltip** next to "Allow fallback knowledge" explaining what it does, that those parts are uncited and can be wrong, and that the question and any attached file reach the model provider even when nothing matched.

### Notes
- The measurement uses the offline embedder and rule-based reasoning, so it never calls a model; its numbers describe file localisation, not answer quality.
