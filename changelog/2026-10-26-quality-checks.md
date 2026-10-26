# Quality checks: saved questions you re-run after every change

### Added
- **Settings → Quality**: save questions with the files a good answer must be built from and the words it must contain; run them again after a change and see the pass rate, the movement since the previous run, what got worse or better, and why each failure failed.
- **Save as a test** under every Ask answer (records the files that answer cited).
- Retrieval runs are free; full runs also have the model write each answer (20 questions at most). A failed model call fails the question.
- Each run records a fingerprint of the index, skills, answer pipeline and search model; the page says which of them changed since the last run.
- `GET /api/golden`, `POST /api/golden/questions`, `DELETE /api/golden/questions/{id}`, `POST /api/golden/run`, `GET /api/golden/runs/{id}`; policy actions `golden.write` and `golden.run` (members); migration 0025.

### Changed
- Retrieval for an answer now lives in one function, `knowledge.retrieve`, used by Ask, the Knowledge page, the Wiki and the quality runs, so a retrieval check tests exactly what an answer is built from.
