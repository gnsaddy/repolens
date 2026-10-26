# FAQ, control status, a fuller footer, and day/night mode before sign-in

### Added
- `/faq`: "Questions about trust", thirteen plain answers (safe? sends my code anywhere? needs a model? trains on my data? who builds it? official channels? production-ready? if the AI is wrong? delete my data? how big a repository? cost? report a problem?).
- Security page: **Where each control stands**, every control marked Built, Partly or Not yet, with the gaps listed (no SAML, no database encryption of our own, no export of everything, no penetration test, no SOC 2 or ISO 27001).
- A footer on the public pages with Product, Trust, Developers and Community columns and a box saying there is no independent audit yet.
- A day/night switch on the sign-in page, the reset page and all public pages. The choice is remembered and shared with the app.
- The statements live in one file, `frontend/src/lib/trust.ts`, so the pages cannot disagree; tests check the known gaps stay marked Not yet and no certification is claimed.
