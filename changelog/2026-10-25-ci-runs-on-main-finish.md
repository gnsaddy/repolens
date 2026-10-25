# CI: runs on main are no longer cancelled by the next push

### Changed
- A newer push still cancels an older run on a branch or pull request, but on `main` every commit now gets a full CI result, so a quick follow-up commit no longer hides whether the earlier one passed.
