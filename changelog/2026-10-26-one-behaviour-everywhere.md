# The same good answer wherever you ask

### Changed
- **Question types** (`services/walkthrough.py`): lookup, explain and build are recognised the same way in the Ask chat, the Knowledge page and the DeepMind Wiki. Explain and build requests also receive the matching route, its handler and what it calls as sources.
- **Build requests** ("write an API to…") are grounded in the repository: first say whether the sources already contain it, follow the shown conventions and imports, mark assumptions, label general knowledge "Illustrative, not from your repository". Never replayed from the answer memory or the exact-prompt cache. Up to 3,000 output tokens.
- **DeepMind Wiki Ask** searches documents and code by default (`docs_only` or the "Documents only" box restores the old behaviour). Wiki and Knowledge now share saved answers.
- **Answer memory** namespace bumped (`PIPELINE = "p2"`): answers produced by the older one-line pipeline are no longer replayed or suggested.
- Answers on the Knowledge and Wiki pages render as **Markdown** (they were plain text).
- **Investigations** that ask for something to be explained or written and describe no failure report "a request, not a failure", claim no root cause, cap confidence at 20% and point to Ask.
