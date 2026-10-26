# Gaps, "Measure it on my repo", a guided start and the fallback tooltip

### Added
- **Gaps** tab in Ask: your own unanswered questions, grouped and counted, each with **Draft a page** (a starter page saved to a documentation collection). `GET /api/chats/gaps`; built only from your private chats.
- **Measure** on a repository: runs the real-bug evaluation on its own history in a separate process and shows top 1 / 3 / 5, a 95% range, the random baseline, hits and misses per fix, and caveats. `POST/GET /api/repositories/{id}/measure`; table `eval_runs` (migration 0022); policy action `repo.measure`.
- Dashboard **Getting started** guide: six steps with a progress bar and a highlighted next step ("Ask your first question", "Measure it on your repo" added); `first_steps` on `/api/dashboard/insights` (counts only).
- **(i) tooltip** next to "Allow fallback knowledge" explaining what it does, that those parts are uncited and can be wrong, and that the question and any attached file reach the model provider even when nothing matched.

### Notes
- The measurement uses the offline embedder and rule-based reasoning, so it never calls a model; its numbers describe file localisation, not answer quality.

### Fixed (Ask)
- Replies that hit the model's output limit were silently cut mid-sentence (the chat budget was 1,000 tokens, which models that "think" first can use up). The budget is now 4,000 and a cut-off reply says so ("Ask it to continue"). `LLMResult.truncated` is set from `finish_reason` / `stop_reason`.
- Diagrams: the model cannot draw images, so it is told to draw flowcharts as plain text in a fenced `text` block, which renders intact.
- A "Not from your documents" badge now appears beside "AI answer" when part of a reply is general knowledge.
