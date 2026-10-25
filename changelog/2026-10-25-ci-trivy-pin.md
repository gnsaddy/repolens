# CI: fix the image scan step

### Fixed
- The `images` CI job failed before it started: the Trivy scan action's tag `0.28.0` no longer exists (its tags now start with `v`). The step is pinned to the commit of `v0.28.0`, so a moved tag cannot change what runs.
