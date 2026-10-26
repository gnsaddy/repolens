# Changelog

One file per release, named after the version (for example `v0.2-beta.md`). Inside, entries are grouped by date, newest first,
and each entry lists what changed under **Added**, **Changed**, **Fixed**, **Security** and **Upgrade notes**. Write for the person
who runs or uses RepoLens, not for the person who wrote the code: what changed, why it matters, and anything they must do.

**Adding an entry:** `make changelog title="short title"` creates a small draft in `unreleased/` from `TEMPLATE.md`; fill it in
(leave out the empty sections), then run `make changelog-index`. That folds the draft into the current version's file, deletes
the draft and rebuilds the list below. Update `docs/features.md`, `docs/architecture.md` and `SECURITY.md` in the same change
when behaviour, design or protections move. Entries are added in the same commit as the change.

## Versions

<!-- versions:start -->
- [v0.2-beta](v0.2-beta.md) · 5 entries · 2026-10-27 to 2026-10-27
- [v0.1-beta](v0.1-beta.md) · 55 entries · 2026-10-20 to 2026-10-27
<!-- versions:end -->
