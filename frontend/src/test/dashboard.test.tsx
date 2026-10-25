import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import Dashboard from "@/pages/Dashboard";

const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const base = { repositories: 2, indexed_repositories: 2, investigations: 5, completed: 4, verified_accuracy: 0.75, reviewed: 4, avg_duration_s: 3.2, recent: [], top_repositories: [] };
const days = Array.from({ length: 14 }, (_, i) => ({ date: `2026-10-${String(i + 10).padStart(2, "0")}`, completed: i % 3, failed: i === 5 ? 1 : 0 }));
const more = (over = {}) => ({
  attention: [{ severity: "high", text: "1 repository failed to index: shop", to: "/repositories" }, { severity: "medium", text: "2 wiki pages may be out of date because the code they describe changed", to: "/wiki?tab=drift" }],
  investigations_per_day: days, week: { started: 6, failed: 1, confirmed: 2 },
  ai: { tokens: 120000, avoided: 60000, savings_rate: 0.333, cost: 0.4, requests: 40, helpful_rate: 0.8, ratings: 10, daily: [{ date: "2026-10-24", tokens: 1000 }, { date: "2026-10-25", tokens: 2000 }] },
  knowledge: { pages: 12, chunks: 340, graph_nodes: 900, graph_edges: 2100, documented_ratio: 0.62, wiki_issues: 3, drift_open: 2, proposals_pending: 1 },
  activity: [{ at: new Date().toISOString(), who: "Ana", what: "created a documentation collection", target: "docs" }], ...over });

function show(insights: unknown, role = "admin") {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const u = String(input);
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "Ana", role, org_id: "o" });
    if (u.endsWith("/dashboard/insights")) return json(insights);
    if (u.endsWith("/dashboard")) return json(base);
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter><Dashboard /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("Dashboard", () => {
  it("puts what needs attention first, each item linking to where to fix it", async () => {
    show(more());
    const card = await screen.findByLabelText("Needs attention");
    expect(within(card).getByRole("link", { name: /failed to index: shop/ })).toHaveAttribute("href", "/repositories");
    expect(within(card).getByRole("link", { name: /may be out of date/ })).toHaveAttribute("href", "/wiki?tab=drift");
    expect(within(card).getByText("2 things")).toBeInTheDocument();
  });
  it("says so when nothing needs attention", async () => {
    show(more({ attention: [] }));
    expect(await screen.findByText("Nothing needs your attention right now.")).toBeInTheDocument();
  });
  it("shows the work trend, AI savings and knowledge-base health", async () => {
    show(more());
    const t = await screen.findByLabelText("Trends");
    expect(within(t).getByText("6 this week")).toBeInTheDocument();
    expect(within(t).getByText(/60\.0K avoided \(33% saved\)/)).toBeInTheDocument();
    expect(within(t).getByText(/80% of 10 rated answers were helpful/)).toBeInTheDocument();
    expect(within(t).getByText("62%")).toBeInTheDocument();
    expect(within(t).getByRole("link", { name: "3 wiki issues" })).toHaveAttribute("href", "/wiki?tab=health");
  });
  it("shows recent activity to admins only", async () => {
    show(more());
    expect(await screen.findByLabelText("Recent activity")).toHaveTextContent("Ana created a documentation collection");
  });
  it("shows no activity card for members", async () => {
    show(more({ activity: [] }), "member");
    await screen.findByLabelText("Needs attention");
    expect(screen.queryByLabelText("Recent activity")).not.toBeInTheDocument();
  });
});
