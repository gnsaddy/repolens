import { execSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { APIRequestContext, Page } from "@playwright/test";

export const PASSWORD = "Quartz-Lantern-2026";
const API = "http://127.0.0.1:18000/api";

export async function registerUser(request: APIRequestContext, email: string, org = "E2E Org") {
  const r = await request.post(`${API}/auth/register`, { data: { email, password: PASSWORD, organization: org, name: "E2E Tester" } });
  if (!r.ok()) throw new Error(`register failed: ${r.status()} ${await r.text()}`);
  return (await r.json()).access_token as string;
}

export const authed = (token: string) => ({ headers: { Authorization: `Bearer ${token}` } });

/** Skip the sign-in screen: the app keeps its token in local storage. */
export async function signedIn(page: Page, token: string) {
  await page.addInitScript((t) => localStorage.setItem("repolens.token", t), token);
}

/** A small git repository with a function that raises KeyError, so an investigation has something to find. */
export function makeRepo(): string {
  const dir = mkdtempSync(join(tmpdir(), "e2e-repo-"));
  mkdirSync(join(dir, "src"));
  writeFileSync(join(dir, "src", "directory.py"), "def rows(records):\n    return [{\"name\": r[\"name\"], \"email\": r[\"email\"]} for r in records]\n");
  writeFileSync(join(dir, "src", "repo.py"), "def find():\n    return [{\"email\": \"a@x.edu\"}]\n");
  const git = (c: string) => execSync(`git -c user.email=e@e.io -c user.name=e ${c}`, { cwd: dir, stdio: "ignore" });
  git("init -q"); git("add -A"); git("commit -q -m initial");
  return dir;
}
