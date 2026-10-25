# Real-bug evaluation, confidence calibration, injection tests

### Added
- `bugs` evaluation suite: scores retrieval and the investigation shortlist on real fix commits from a repository's own history, with no download. First measured result on this project (10 cases): true file in the top 3 for 30% (retrieval) and 40% (investigation), against 2% for random. `make eval-bugs repo=PATH`.
- Insights card and `GET /api/dashboard/calibration`: reliability of the confidence score against reviews, Brier score, calibration error and, after 20 reviews, a fitted correction.
- `tests/test_injection.py` red-team suite and a frontend guard test against raw HTML and unsafe links.

### Security
- A drafted fix that adds command execution, evaluation of strings or network calls is now dropped.

### Fixed
- The MCP & tokens tab could briefly offer write tokens to a viewer before the signed-in user had loaded; it now waits for the user.
