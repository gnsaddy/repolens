# Browser end-to-end tests

### Added
- Playwright tests (`frontend/e2e`, `npm run e2e`, `make e2e`) that start the real API on a throwaway database and the real web app, then click through sign-in and sign-out, a wrong password, the "what leaves this server" panel, wiki search and ask with the rating buttons, an investigation turned into a reviewable runbook proposal (the page that used to be blank), the profile sessions list and the Insights server load.
- A CI job that installs a browser with its system libraries and runs them after the frontend checks.

### Upgrade notes
- Running them locally needs a Chromium and its system libraries: `npx playwright install --with-deps chromium`, or set `E2E_CHROMIUM` to a browser you already have. They were not run on the development machine (its cached Chromium lacks system libraries) so the first CI run is the first execution.
