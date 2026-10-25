# Security hardening, Insights, evaluation suites and feedback

### Added
- Request guard: security headers, 32 MB body limit, rate limits per address, per session and for model-backed calls; the client address is read from a configurable number of trusted proxies (`REPOLENS_TRUSTED_PROXY_HOPS`).
- Password policy (common passwords, e-mail name, length), audit trail for failed sign-ins, sessions end after eight idle hours.
- Insights: live server load (CPU, memory, network, request rate and latency), savings view, answer quality; models with no use are hidden; proper currency formatting.
- Evaluation suites for documents, answer memory and routing with a recorded baseline and a CI gate (`make eval`).
- Thumbs up/down on answers; admin export of rated questions.
- Embedding-model change detection and `python -m repolens.devtools.reembed`.
- Knowledge → Memory tab: browse, deep-search, forget, and export selected answers as a skill.
- Investigations stop after `REPOLENS_INVESTIGATION_TIMEOUT_S`, stuck runs are failed on restart, and failed runs can be run again.
- Investigations read KeyError, None, IndexError and divide-by-zero errors against the retrieved code.
- Kubernetes: the API waits for PostgreSQL, patient database probes, SSH egress for git clones.

### Changed
- The local embedder is about twice as fast (identical vectors). The main web bundle is a third of its previous size.
- API docs exist only in development; `/metrics` can require a token.

### Fixed
- Blank Proposals and "Draft a runbook" pages (the API did not return `apply_options`).
- Embeddings failed against keyless local servers such as Ollama.

### Security
- Stricter Content-Security-Policy, HSTS and permissions policy in nginx; links from stored data must be http(s).

### Upgrade notes
- Set `REPOLENS_TRUSTED_PROXY_HOPS` to the number of proxies in front of the API (Kubernetes manifests use 2) or per-address limits treat everyone behind the web container as one caller.
- Production needs a signing secret of at least 32 characters.
