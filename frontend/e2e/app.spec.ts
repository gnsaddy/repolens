import { expect, test } from "@playwright/test";
import { authed, makeRepo, PASSWORD, registerUser, signedIn } from "./helpers";

const API = "http://127.0.0.1:18000/api";
const email = `e2e-${Date.now()}@example.test`;
let token = "";

test.beforeAll(async ({ request }) => { token = await registerUser(request, email); });

test("sign in and sign out use real sessions", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(/Welcome back/)).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.goto("/");  // the old token no longer works: the session ended on the server
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("a wrong password is refused and says so", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("not-the-password-1");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("Invalid email or password");
});

test("Settings says nothing leaves this server on a fresh install", async ({ page }) => {
  await signedIn(page, token);
  await page.goto("/settings?tab=ai");
  await expect(page.getByText("What leaves this server")).toBeVisible();
  await expect(page.getByText("Nothing leaves", { exact: true })).toBeVisible();
});

test("the wiki finds, edits and answers from a page", async ({ page, request }) => {
  const c = await (await request.post(`${API}/knowledge/collections`, { ...authed(token), data: { name: "ops" } })).json();
  await request.post(`${API}/knowledge/collections/${c.id}/documents`, { ...authed(token), data: { documents: [{ path: "retry.md", content: "# Retry policy\n\nThe payment service retries failed requests three times with exponential backoff.\n" }] } });
  await signedIn(page, token);
  await page.goto("/wiki");
  await page.getByLabel("Search pages").fill("how many times are payments retried");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("list", { name: "Search results" }).getByText("Retry policy")).toBeVisible();
  await page.getByRole("tab", { name: /Ask/ }).click();
  await page.getByLabel("Question").fill("How many times does the payment service retry?");
  await page.getByRole("button", { name: /^Ask/ }).click();
  await expect(page.getByText("Answer", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Helpful" })).toBeVisible();
});

test("an investigation becomes a runbook proposal that can be reviewed (no blank page)", async ({ page, request }) => {
  const repo = await (await request.post(`${API}/repositories`, { ...authed(token), data: { name: "dir", source: "local", url: makeRepo() } })).json();
  for (let i = 0; i < 30; i++) {
    const r = await (await request.get(`${API}/repositories`, authed(token))).json();
    if (r.find((x: { id: string; status: string }) => x.id === repo.id)?.status === "ready") break;
    await page.waitForTimeout(500);
  }
  const inv = await (await request.post(`${API}/investigations`, { ...authed(token), data: { query: "KeyError: 'name' in rows", repo_ids: [repo.id] } })).json();
  await request.post(`${API}/investigations/${inv.id}/review`, { ...authed(token), data: { outcome: "confirmed" } });
  await signedIn(page, token);
  await page.goto(`/investigations/${inv.id}`);
  await page.getByRole("button", { name: /Draft a runbook/ }).click();
  await expect(page).toHaveURL(/tab=proposals/);
  await expect(page.getByRole("button", { name: /Approve and create/ })).toBeVisible();  // used to be a blank page
  await expect(page.getByText("This page hit an error")).toHaveCount(0);
});

test("profile shows this device and Insights shows the server", async ({ page }) => {
  await signedIn(page, token);
  await page.goto("/profile");
  await expect(page.getByRole("list", { name: "Sessions" })).toContainText("this device");
  await page.goto("/insights");
  await expect(page.getByRole("region", { name: "Server load" })).toBeVisible();
  await expect(page.getByRole("region", { name: "AI utilisation" })).toBeVisible();
});

test("a Jira ticket cannot be opened before Jira is connected, and says why", async ({ page }) => {
  await signedIn(page, token);
  await page.goto("/jira");
  await expect(page.getByText("Connect Jira first")).toBeVisible();
});
