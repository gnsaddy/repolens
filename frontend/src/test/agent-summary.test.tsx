import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentSummary from "@/components/AgentSummary";
import type { AgentSummary as Summary } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

const idle: Summary = {
  paused: false, rules_on: 0, runs_24h: 0, failed_24h: 0, working_now: 0, waiting: 0, tokens_used: 0, token_budget: 0, last_run: null,
  by_event: { ci_failure: { runs: 0, failed: 0 }, pr_review: { runs: 0, failed: 0 }, drift_draft: { runs: 0, failed: 0 }, watch: { runs: 0, failed: 0 } },
};
const busy: Summary = {
  ...idle, rules_on: 2, runs_24h: 5, failed_24h: 1, working_now: 1, waiting: 3, tokens_used: 41230, token_budget: 100000,
  by_event: { ...idle.by_event, ci_failure: { runs: 2, failed: 1 }, pr_review: { runs: 3, failed: 0 } },
  last_run: { title: "PR #7: Fix the user lookup", event: "pr_review", status: "started", created_at: new Date().toISOString() },
};

function show(data: Summary | null, role = "admin", detailed = false) {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const u = String(input);
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "Ana", role, org_id: "o" });
    if (u.endsWith("/automations/summary")) return data ? json(data) : json({}, 403);
    return json({}, 404);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter><AgentSummary detailed={detailed} /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("agent summary", () => {
  it("says it is off, and what turning it on does, when it has never been used", async () => {
    show(idle);
    const card = await screen.findByLabelText("Agent");
    expect(card).toHaveTextContent("It is off");
    expect(card).toHaveTextContent("nothing reaches GitHub unless you approve it");
    expect(within(card).getByText("off")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open the agent" })).toHaveAttribute("href", "/agent");
  });

  it("shows what it did in the last day, what waits, and the last run", async () => {
    show(busy);
    const card = await screen.findByLabelText("Agent");
    expect(within(card).getByText("2 rules on")).toBeInTheDocument();
    expect(card).toHaveTextContent("Runs, last day");
    expect(card).toHaveTextContent("1 going on now");
    expect(within(card).getByText("Waiting in the Inbox").parentElement).toHaveTextContent("3");
    expect(card).toHaveTextContent("41,230");
    expect(card).toHaveTextContent("of a budget of 100,000");
    expect(card).toHaveTextContent("Last run: PR #7: Fix the user lookup · started");
    expect(within(card).getByRole("link", { name: "See why" })).toHaveAttribute("href", "/agent");
    expect(within(card).getByRole("link", { name: "Read them" })).toHaveAttribute("href", "/agent");
  });

  it("shows a paused agent", async () => {
    show({ ...busy, paused: true });
    expect(within(await screen.findByLabelText("Agent")).getByText("paused")).toBeInTheDocument();
  });

  it("adds the runs by kind and the budget on Insights", async () => {
    show(busy, "admin", true);
    const t = await screen.findByRole("table", { name: "Agent runs by kind" });
    expect(within(t).getByText("CI failures").closest("tr")).toHaveTextContent("2");
    expect(within(t).getByText("Pull request reviews").closest("tr")).toHaveTextContent("3");
    expect(screen.getByLabelText("Agent")).toHaveTextContent("Token budget: 41,230 of 100,000 used");
  });

  it("is not shown to people who are not admins", async () => {
    show(busy, "member");
    await screen.findByText("", { selector: "div", exact: false }).catch(() => null);
    expect(screen.queryByLabelText("Agent")).toBeNull();
  });

  it("shows nothing when the server does not have it yet", async () => {
    show(null);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByLabelText("Agent")).toBeNull();
  });
});
