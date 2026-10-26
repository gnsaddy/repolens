# Indexing no longer slows down disproportionately on big repositories

### Fixed
- Building the knowledge graph resolved every import by scanning every file path in the repository, so indexing time grew with files x imports x files. Indexing a large repository (the Kubernetes source took more than 30 minutes) was far slower than its size explained. Import resolving now uses lookup tables built once per repository (`PathIndex` in `services/graph.py`), with identical results.
- Measured on a 3,075-file, 24,150-chunk project: indexing 127.7 s to 49.8 s, graph building 93 s to 15.6 s, and the time per chunk stays flat as the repository grows (2.4 ms at 2,400 chunks, 2.1 ms at 24,000).

### Added
- `tests/test_import_index.py`: the lookups return exactly what the old scan did, and one lookup does not get slower as the repository grows 100-fold.
- A section in `docs/operations.md` on how long indexing takes and how to size a beta.
