import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import type { RiskAssessment } from "@/lib/api";
import Risk, { Gauge, RiskResult } from "@/pages/Risk";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const dep = (name: string, cross = false) => ({ node_id: name, type: "function", name, path: `src/${name}.py`, repo: cross ? "billing" : "shop", cross_repo: cross, rel: "CALLS", target: "compute_total", evidence: "", provenance: "static_analysis" });
const assessment: RiskAssessment = {
  id: "a1", repo_id: "r1", ref_type: "commit", ref: "ef7278dbcafe", title: "refactor: compute_total", score: 62.4, level: "high", model_version: "oracle-heuristic-v1", created_at: new Date().toISOString(),
  limitations: ["A prioritisation heuristic, not a calibrated probability of failure."],
  signals: [
    { key: "size", label: "Change size", weight: 0.15, value: 0.2, points: 3, detail: "12 lines changed across 1 files", evidence: [] },
    { key: "centrality", label: "Dependency centrality", weight: 0.2, value: 0.9, points: 18, detail: "9 direct dependents (2 in other repositories)", evidence: [{ name: "make_invoice", repo: "billing" }] },
    { key: "tests", label: "Test coverage gap", weight: 0.15, value: 1, points: 15, detail: "0/1 changed code files have tests that reference them", evidence: [] },
  ],
  affected: { files: [], symbols: [{ name: "compute_total", path: "shop/orders.py", type: "function" }], dependents: [dep("checkout"), dep("make_invoice", true)], tests_to_run: [], docs_to_update: [{ ...dep("Pricing"), type: "section", path: "docs/pricing.md", name: "Pricing" }], symbol_level: true },
};

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("RiskResult", () => {
  it("shows signals sorted by contribution, the blast radius, and missing tests", async () => {
    render(<RiskResult r={assessment} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Dependency centrality"); // highest points first
    expect(screen.getByText("make_invoice")).toBeInTheDocument();
    expect(screen.getByText("other repo")).toBeInTheDocument();
    expect(screen.getByText("No tests reference this code.")).toBeInTheDocument();
    expect(screen.getByText(/docs\/pricing.md/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Dependency centrality/ }));
    expect(screen.getByText(/repo: billing/)).toBeInTheDocument(); // evidence expands
    expect(screen.getByText(/not a calibrated probability/)).toBeInTheDocument();
  });
  it("labels the gauge as a prioritisation score", () => {
    render(<Gauge score={62.4} level="high" />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "62");
    expect(screen.getByText(/not a probability of failure/)).toBeInTheDocument();
  });
});

describe("Risk page", () => {
  it("scans recent commits and opens an assessment when one is clicked", async () => {
    localStorage.setItem("repolens.token", "tok");
    const posted: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.endsWith("/repositories")) return json([{ id: "r1", name: "shop", source: "local", url: "", default_branch: "main", indexed_sha: "x", status: "ready", file_count: 1, chunk_count: 1, updated_at: new Date().toISOString() }]);
      if (u.includes("/risk/scan")) return json([{ sha: "ef7278dbcafe0000", title: "refactor: compute_total", author: "d", committed_at: new Date().toISOString(), score: 62.4, level: "high", top_signals: ["Dependency centrality"] }, { sha: "aaaa1111bbbb", title: "docs: update", author: "d", committed_at: new Date().toISOString(), score: 1.2, level: "low", top_signals: [] }]);
      if (u.includes("/risk?")) return json([]);
      if (u.endsWith("/risk/assess")) { posted.push(JSON.parse(init!.body as string)); return json(assessment, 201); }
      return json({}, 404);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter><Risk /></MemoryRouter></AuthProvider></QueryClientProvider>);
    const scanCard = (await screen.findByText("Riskiest recent commits")).closest("div")!.parentElement!;
    const first = await within(scanCard).findByRole("button", { name: /refactor: compute_total/ });
    await userEvent.click(first);
    expect(await screen.findByText("Why this score")).toBeInTheDocument();
    expect(posted).toEqual([{ repo_id: "r1", sha: "ef7278dbcafe0000" }]);
  });
});
