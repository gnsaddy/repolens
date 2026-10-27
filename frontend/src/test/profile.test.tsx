import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Insights from "@/pages/Insights";
import Profile from "@/pages/Profile";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const me = { id: "1", email: "sam@acme.io", name: "Sam", role: "admin", org_id: "o", auth_source: "password" };
const sessions = [
  { id: "s1", device: "Chrome on Windows", ip: "10.0.0.2", method: "password", created_at: new Date().toISOString(), last_seen_at: new Date().toISOString(), current: true },
  { id: "s2", device: "Safari on iOS", ip: "10.0.0.9", method: "password", created_at: new Date().toISOString(), last_seen_at: new Date().toISOString(), current: false }];

function renderPage(el: React.ReactNode) {
  const calls: { url: string; method?: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (init?.method && init.method !== "GET") calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (u.endsWith("/auth/me") && init?.method === "PATCH") return json({ ...me, name: JSON.parse(String(init.body)).name });
    if (u.endsWith("/auth/me")) return json(me);
    if (u.endsWith("/auth/sessions")) return json(sessions);
    if (u.includes("/auth/sessions/")) return json(null, 204);
    if (u.endsWith("/auth/password")) return json(null, 204);
    if (u.includes("/feedback/summary")) return json({ days: 30, total: 10, helpful_rate: 0.8, by_kind: { answer: { up: 8, down: 2 } }, from_memory: { total: 4, helpful_rate: 0.75 } });
    if (u.includes("/dashboard/calibration")) return json({ status: "too_few", message: "3 reviewed so far. At least 20 are needed.", reviewed: 3, min_for_curve: 20, brier: 0.31, ece: 0.4, bias: "overconfident", bands: [{ from: 0.8, to: 1, n: 3, mean_confidence: 0.86, confirmed_rate: 0.33 }, { from: 0, to: 0.2, n: 0, mean_confidence: null, confirmed_rate: null }] });
    if (u.includes("/metrics/system")) return json({ available: true, cores: 8, load_average: [0.5, 0.6, 0.7], uptime_s: 7300, threads: 24, interval_s: 5,
      current: { t: 1, host_cpu: 37.2, process_cpu: 12.5, mem_used: 5_000_000_000, mem_total: 16_000_000_000, process_rss: 120_000_000, rx_bps: 2048, tx_bps: 4096, rps: 1.4, error_rate: 0, p50_ms: 8, p95_ms: 41, container_limit: null },
      history: [{ t: 1, host_cpu: 30, process_cpu: 10, mem_used: 5e9, mem_total: 16e9, process_rss: 1e8, rx_bps: 1000, tx_bps: 2000, rps: 1, error_rate: 0, p50_ms: 8, p95_ms: 40, container_limit: null }] });
    if (u.includes("/metrics/health")) return json({ version: "v0.2-beta", ok: false, problems: ["The retention loop is late", "1 repository failed to index"],
      jobs: [{ name: "daily-reindex", state: "ok", every_s: 86400, last_ok: Date.now() / 1000 - 3600, failures: 0, last_error: null }, { name: "retention", state: "late", every_s: 86400, last_ok: null, failures: 0, last_error: null }],
      repositories: { pending: 0, indexing: 1, ready: 3, error: 1 }, quality: { passed: 4, total: 5, trigger: "reindex", mode: "retrieval", at: new Date().toISOString() } });
    if (u.includes("/metrics/overview")) return json({ days: 30, content: { repositories: 2, doc_collections: 1, pages: 12, chunks: 340, graph_nodes: 900, graph_edges: 2100, indexing_errors: 0 },
      work: { investigations: 5, completed: 4, failed: 1, confirmed: 2, avg_minutes: 1.5, proposals_pending: 1, proposals_applied: 3, drift_open: 2 },
      ai: { requests: 40, tokens: 120000, cost: 0.42, errors: 1, cache_hits: 6, tokens_avoided: 60000, savings_rate: 0.333, by_model: [{ name: "gemini/flash-lite", input_tokens: 80000, output_tokens: 10000, cached_input_tokens: 0, requests: 30, errors: 0, cost: 0.1, avg_latency_ms: 300 }, { name: "memory/memory", input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, requests: 4, errors: 0, cost: 0, avg_latency_ms: 0 }], by_agent: [], daily: [] },
      memory: { enabled: true, entries: 4, hits: 7, tokens_saved: 9000, stored: 4 }, team: { users: 3, active_sessions: 2, active_users_period: 3 } });
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter>{el}</MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("Profile", () => {
  it("lists sessions, ends another one, and never offers to end the current one", async () => {
    const calls = renderPage(<Profile />);
    const list = await screen.findByRole("list", { name: "Sessions" });
    expect(within(list).getByText("this device")).toBeInTheDocument();
    expect(within(list).getAllByRole("button", { name: "End session" })).toHaveLength(1);
    await userEvent.click(within(list).getByRole("button", { name: "End session" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/auth/sessions/s2") && c.method === "DELETE")).toBe(true));
  });
  it("saves the display name", async () => {
    const calls = renderPage(<Profile />);
    const box = await screen.findByLabelText("Display name");
    await userEvent.clear(box); await userEvent.type(box, "Sam Lee");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Sam Lee" }));
  });
  it("checks the new password twice before sending", async () => {
    const calls = renderPage(<Profile />);
    await userEvent.type(await screen.findByLabelText("Current password"), "old-password-1");
    await userEvent.type(screen.getByLabelText("New password"), "brand-new-pass-2");
    await userEvent.type(screen.getByLabelText("Repeat new password"), "brand-new-pass-3");
    expect(screen.getByRole("alert")).toHaveTextContent("do not match");
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Repeat new password"));
    await userEvent.type(screen.getByLabelText("Repeat new password"), "brand-new-pass-2");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/auth/password"))?.body).toEqual({ current_password: "old-password-1", new_password: "brand-new-pass-2" }));
  });
});

describe("Insights", () => {
  it("shows AI utilisation, savings, work done and what is indexed", async () => {
    renderPage(<Insights />);
    const ai = await screen.findByRole("region", { name: "AI utilisation" });
    expect(within(ai).getByText("Tokens avoided")).toBeInTheDocument();
    expect(within(ai).getByText("33% of what would have been used")).toBeInTheDocument();
    expect(within(ai).getByText("gemini/flash-lite")).toBeInTheDocument();
    expect(within(ai).queryByText("memory/memory")).not.toBeInTheDocument(); // a model that used no tokens and cost nothing is not listed
    expect(within(ai).getByText("$0.42")).toBeInTheDocument();
    expect(within(ai).getByLabelText("Answer quality")).toHaveTextContent("80% of rated answers were helpful");
    expect(within(screen.getByRole("region", { name: "Work done" })).getByText("1.5 min")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Knowledge base" })).getByText("900 nodes")).toBeInTheDocument();
  });
  it("shows whether the confidence score matched what people confirmed", async () => {
    renderPage(<Insights />);
    const c = await screen.findByLabelText("Confidence check");
    expect(c).toHaveTextContent("3 reviewed so far");
    const t = within(c).getByRole("table", { name: "Confidence against outcome" });
    expect(within(t).getByText("80–100%")).toBeInTheDocument();
    expect(within(t).getByText("33%")).toBeInTheDocument(); // actually confirmed
    expect(within(t).queryByText("0–20%")).not.toBeInTheDocument(); // empty bands are not listed
    expect(c).toHaveTextContent("overconfident");
  });
  it("shows how busy the server is: CPU, memory, network and request rate", async () => {
    renderPage(<Insights />);
    const load = await screen.findByRole("region", { name: "Server load" });
    expect(within(load).getByText("37%")).toBeInTheDocument();
    expect(within(load).getByText(/of 8 cores/)).toBeInTheDocument();
    expect(within(load).getByRole("meter", { name: "CPU used" })).toHaveAttribute("aria-valuenow", "37");
    expect(within(load).getByText("31%")).toBeInTheDocument(); // 5.0 of 16 GB
    expect(within(load).getByText(/↓ 2\.0 KB\/s/)).toBeInTheDocument();
    expect(within(load).getByText(/p95 41 ms/)).toBeInTheDocument();
  });
  it("tells an admin when a background job is late, and what the last saved-question check found", async () => {
    renderPage(<Insights />);
    const h = await screen.findByRole("region", { name: "RepoLens health" });
    expect(within(h).getByText("needs attention")).toBeInTheDocument();
    expect(within(h).getByRole("alert")).toHaveTextContent("The retention loop is late");
    expect(within(h).getByText("Daily re-index")).toBeInTheDocument();
    expect(within(h).getByText("late")).toBeInTheDocument();
    expect(h).toHaveTextContent("Saved questions: 4 of 5 passed");
    expect(h).toHaveTextContent("checked by itself after indexing");
  });
});
