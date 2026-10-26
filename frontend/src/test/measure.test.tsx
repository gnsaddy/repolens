import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import MeasurePanel from "@/components/MeasurePanel";
import type { MeasureRun, Repo } from "@/lib/api";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
afterEach(() => vi.restoreAllMocks());
const repo: Repo = { id: "r1", name: "user-service", source: "local", url: "/x", default_branch: "main", indexed_sha: "abc", status: "ready", file_count: 5, chunk_count: 20, updated_at: new Date().toISOString() };
const done: MeasureRun = {
  id: "m1", status: "completed", cases: 10, error: null, started_at: new Date().toISOString(), finished_at: new Date().toISOString(),
  result: { cases: 4, random_top3: 0.1, retrieval: { top1: 0.25, top3: 0.5, top5: 0.5, mrr: 0.4 }, investigation: { top1: 0.5, top3: 0.75, top5: 0.75, mrr: 0.6 }, top3_ci95: [0.3, 0.95], caveats: ["4 case(s) is a small sample: read the range, not just the headline."],
    rows: [{ commit: "abc12345", report: "fix paging", files: ["a.py"], retrieval_rank: 2, investigation_rank: 1 }, { commit: "def67890", report: "fix retry", files: ["b.py"], retrieval_rank: null, investigation_rank: null }] },
};
function open(items: MeasureRun[], canRun = true) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    calls.push(`${init?.method ?? "GET"} ${String(input)}`);
    if (init?.method === "POST") return json({ ...done, status: "running", result: null }, 202);
    return json({ items, can_run: canRun });
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><MeasurePanel repo={repo} onClose={() => undefined} /></MemoryRouter></QueryClientProvider>);
  return calls;
}

describe("Measure on my repo", () => {
  it("shows the last result with the range, the misses and the caveats", async () => {
    open([done]);
    const res = await screen.findByLabelText("Measurement result");
    expect(res).toHaveTextContent("75%");
    expect(res).toHaveTextContent("between 30% and 95%");
    expect(res).toHaveTextContent("about 10% in the top 3");
    expect(res).toHaveTextContent("found #1");
    expect(res).toHaveTextContent("missed");
    expect(res).toHaveTextContent("small sample");
    expect(screen.getByText(/no code leaves your server/)).toBeInTheDocument();
  });
  it("starts a run with the chosen number of bug fixes", async () => {
    const calls = open([]);
    await userEvent.selectOptions(await screen.findByLabelText("Bug fixes to test"), "15");
    await userEvent.click(screen.getByRole("button", { name: "Measure now" }));
    await waitFor(() => expect(calls).toContain(`POST /api/repositories/r1/measure`));
  });
  it("explains why a repository that is not indexed cannot be measured, and shows a failure", async () => {
    open([], false);
    expect(await screen.findByText(/Index this repository first/)).toBeInTheDocument();
  });
  it("reports a failed run", async () => {
    open([{ ...done, status: "failed", result: null, error: "No usable bug fixes were found" }]);
    expect(await screen.findByRole("alert")).toHaveTextContent("No usable bug fixes");
  });
});
