# CI: fix the image scan step

### Fixed
- The `images` CI job failed before it started. First the Trivy scan action's tag `0.28.0` no longer existed (tags now start with `v`); pinning the commit of `v0.28.0` then failed too, because that version loads a helper action (`setup-trivy@v0.2.1`) by a tag that was also removed. The step now uses `trivy-action` v0.36.0, pinned to its commit, which loads its helper by commit as well, so a moved or deleted tag cannot break or change the scan. The inputs we use (`image-ref`, `severity`, `exit-code`, `ignore-unfixed`) are unchanged.
