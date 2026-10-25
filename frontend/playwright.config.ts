import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const API = 18000, WEB = 15173;
const data = mkdtempSync(join(tmpdir(), "repolens-e2e-"));

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],  // "github" turns failures into annotations on the run
  use: { baseURL: `http://127.0.0.1:${WEB}`, trace: "retain-on-failure", launchOptions: process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {} },
  // the real API on a throwaway SQLite database, and the real web app proxying to it
  webServer: [
    {
      command: `../backend/.venv/bin/python -m uvicorn repolens.main:app --port ${API}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API}/healthz`,
      timeout: 60_000,
      reuseExistingServer: false,
      env: {
        REPOLENS_ENV: "dev",
        REPOLENS_DATABASE_URL: `sqlite:///${join(data, "e2e.db")}`,
        REPOLENS_REPOS_DIR: join(data, "clones"),
        REPOLENS_JWT_SECRET: "e2e-secret-e2e-secret-e2e-secret-0123",
        REPOLENS_RUN_JOBS_INLINE: "true",
        REPOLENS_LLM_PROVIDER: "heuristic",
        REPOLENS_RATE_LIMIT_ENABLED: "false",
      },
    },
    {
      command: `npx vite --port ${WEB} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${WEB}`,
      timeout: 60_000,
      reuseExistingServer: false,
      env: { VITE_API_PROXY: `http://127.0.0.1:${API}` },
    },
  ],
});
