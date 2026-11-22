import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import GitHubConnection from "@/components/GitHubConnection";
import type { AgentWebhook } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

const events = [{ event: "Pull requests", for: "reviewing each new pull request" }, { event: "Workflow runs", for: "investigating a failed CI run" }];
const waiting: AgentWebhook = { secret_set: true, path: "/api/webhooks/github", events, last_delivery_at: null, deliveries: [] };
const working: AgentWebhook = {
  ...waiting, last_delivery_at: new Date().toISOString(),
  deliveries: [
    { id: "1", event: "pull_request", action: "opened", repo: "acme/shop", outcome: "ok", note: "review=1", at: new Date().toISOString() },
    { id: "2", event: "push", action: "", repo: "", outcome: "rejected", note: "The signature did not match: the secret on GitHub differs from the one on the server", at: new Date().toISOString() },
  ],
};

function show(data: AgentWebhook) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "Ana", role: "admin", org_id: "o" });
    if (u.endsWith("/automations/webhook/test")) { calls.push(`${init?.method} test`); return json({ ...data, deliveries: [{ id: "9", event: "selftest", action: "ping", repo: "", outcome: "ok", note: "The secret is set and the receiver works (a self test, not sent by GitHub)", at: new Date().toISOString() }] }); }
    if (u.endsWith("/automations/webhook")) return json(data);
    return json({}, 404);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><GitHubConnection /></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("GitHub connection", () => {
  it("says what to set on GitHub and that it is waiting for the first delivery", async () => {
    show(waiting);
    const card = await screen.findByLabelText("GitHub connection");
    expect(await within(card).findByText("Waiting for the first delivery from GitHub")).toBeInTheDocument();
    expect(card).toHaveTextContent("/api/webhooks/github");
    expect(card).toHaveTextContent("application/json");
    expect(card).toHaveTextContent("REPOLENS_GITHUB_WEBHOOK_SECRET");
    expect(card).toHaveTextContent("Pull requests, Workflow runs");
    expect(card).toHaveTextContent("Nothing has arrived yet");
    expect(card).toHaveTextContent("which GitHub cannot reach"); // the test page is on localhost
  });

  it("shows what GitHub sent, with the reason a delivery was refused", async () => {
    show(working);
    const card = await screen.findByLabelText("GitHub connection");
    expect(await within(card).findByText(/GitHub reached RepoLens/)).toBeInTheDocument();
    const list = within(card).getByRole("list", { name: "Deliveries" });
    expect(list).toHaveTextContent("pull_request · opened");
    expect(list).toHaveTextContent("acme/shop");
    expect(list).toHaveTextContent("secret on GitHub differs");
  });

  it("turns red when the newest delivery was refused, even if an earlier one worked", async () => {
    const refusedFirst = { ...working, deliveries: [{ ...working.deliveries[1] }, working.deliveries[0]] };
    show(refusedFirst);
    const card = await screen.findByLabelText("GitHub connection");
    expect(await within(card).findByText(/The latest delivery was refused/)).toBeInTheDocument();
    expect(within(card).queryByText(/GitHub reached RepoLens/)).toBeNull();
  });

  it("checks the receiver and says it is not from GitHub", async () => {
    const calls = show(waiting);
    const card = await screen.findByLabelText("GitHub connection");
    await userEvent.click(await within(card).findByRole("button", { name: "Check the receiver" }));
    await waitFor(() => expect(calls).toEqual(["POST test"]));
    expect(await within(card).findByText(/a self test, not sent by GitHub/)).toBeInTheDocument();
  });

  it("says so, and cannot check, when no secret is set", async () => {
    show({ ...waiting, secret_set: false });
    const card = await screen.findByLabelText("GitHub connection");
    expect(await within(card).findByText("No webhook secret is set on the server")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Check the receiver" })).toBeDisabled();
  });
});
