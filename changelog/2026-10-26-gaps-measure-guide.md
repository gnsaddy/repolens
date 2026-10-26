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
- The Ask message box now clears the moment a question is sent (it stayed filled until the answer arrived); if sending fails, the question and any attached file are put back.

### Fixed: "list all the API endpoints"
- Asking Ask to list every endpoint returned one or two, because chat only reads the best few search matches and route decorators sit outside the indexed function chunks. Each source file's routes (method, path, handler, line; FastAPI, Flask, Express, Spring, Go) are now written down at index time as a `routes` chunk, and "list/all/every … endpoints/routes" questions are answered from that full inventory with citations. Re-index existing repositories once. New `services/routes.py`.
- When a "list all endpoints" question finds no route inventory (the repository was indexed before this existed), the answer now says so and tells you to re-index, instead of presenting a partial list as complete.

### Fixed: a failed model call looked like an answer
- When the language model failed (bad key, quota, budget, timeout, unreachable), Ask silently showed a few loosely matching passages as if they were the answer. It now says **"The AI model could not answer"** with the reason and the model tried, shows a red **AI unavailable** badge, lists the matching passages as "not a written answer", and offers **Try again**. Message mode `failed`.
- Extracted passages are limited to ones sharing at least half of the question's meaningful words.

### Fixed: browser tests failing in CI since the chat commit
- The floating Ask button was labelled "Ask a question", which also matched the wiki question box's `getByLabel("Question")`, so the browser test "the wiki finds, edits and answers from a page" found two elements and failed. The button is now labelled "Open Ask chat", and a browser test checks that the question box stays unique.
