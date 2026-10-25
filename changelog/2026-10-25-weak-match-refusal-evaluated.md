# Refusing weak document matches: measured, not shipped

### Changed
- The document evaluation suite now measures what refusing weak matches would do ("the documents do not cover this") at two thresholds.

### Findings
- Refusing when fewer than a quarter of the question's words appear in the best passages catches 88% of questions the documents cannot answer, but wrongly refuses 58% of valid re-worded questions (31% of all valid questions). With the local embedder, re-worded questions find their page through vectors and share few words with it, so a word-coverage rule cannot tell them from uncovered ones.
- No change was made to answering. The model is still told to say when the sources do not cover a question, and a cheap model's answer that neither cites nor says so is retried on a stronger one.
- Revisit when an embedding model is connected: its raw similarity score should separate the two cases, and the suite will show whether it does.
