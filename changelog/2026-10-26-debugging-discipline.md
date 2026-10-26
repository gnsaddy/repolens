# RCA reports follow a debugging discipline

Adopted from the `debugging-and-error-recovery` and `doubt-driven-development` skills in addyosmani/agent-skills (read only; nothing copied or installed).

### Added
- **How to confirm it** checklist in every report: reproduce, localise (with a `git bisect` hint), reduce, fix the cause, guard with a regression test, verify.
- **Red flags** section: no trace, no commit link, no covering test, near-tied hypotheses, low confidence.
- **Independent review** (needs a model): a fresh pass sees only the incident and evidence, not our conclusion, and reports agree/differ, alternatives, assumptions and the quickest check. Skipped in savings mode at 70%+ confidence; skipped or failed reviews are reported with the reason. New `challenge` step in the workflow, `verification.review` on the investigation.
- The report's "Validation" lines now reflect the review instead of a fixed "not run".
