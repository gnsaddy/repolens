import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dailyTokenSeries } from "@/lib/utils";
import TokenUsage from "@/pages/TokenUsage";

afterEach(() => vi.restoreAllMocks());

describe("tokens per day", () => {
  it("uses the server's single tokens figure and never produces NaN", () => {
    expect(dailyTokenSeries([{ date: "2026-10-20", tokens: 1_400_000 }])).toEqual([{ date: "10-20", tokens: 1_400_000 }]);
    expect(dailyTokenSeries([{ date: "2026-10-22", input_tokens: 90, output_tokens: 10 }])[0].tokens).toBe(100); // older shape
    expect(dailyTokenSeries([{ date: "2026-10-25" }])[0].tokens).toBe(0); // missing figures are zero, not NaN
    expect(dailyTokenSeries(undefined as never)).toEqual([]);
  });
});

describe("unpriced models", () => {
  it("tells people where to set prices", async () => {
    const group = { name: "gemini/x", input_tokens: 10, output_tokens: 5, cached_input_tokens: 0, cost: 0, requests: 3, errors: 0, avg_latency_ms: 20 };
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      days: 30, pricing_version: "x", budgets: { daily: 0, monthly: 0 },
      totals: { input_tokens: 10, output_tokens: 5, cost: 0, requests: 3, errors: 0, cache_hit_rate: 0, error_rate: 0, tokens_avoided: 0, cost_per_successful_rca: null, successful_investigations: 0 },
      vs_previous: { tokens: null, cost: null }, by_model: [group], by_agent: [group], by_workflow: [group], daily: [{ date: "2026-10-20", tokens: 15, cost: 0 }] }), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><TokenUsage /></MemoryRouter></QueryClientProvider>);
    const link = await screen.findByRole("link", { name: /Settings → AI & search/ });
    expect(link).toHaveAttribute("href", "/settings?tab=ai");
    expect(screen.getByText(/price per 1M tokens/)).toBeInTheDocument();
  });
});
