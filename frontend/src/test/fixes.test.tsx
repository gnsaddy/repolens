import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FixProposals from "@/components/FixProposals";
import { FeedbackProvider } from "@/components/feedback";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const fix = (over = {}) => ({
  id: "abcdef123456", title: "Guard the lookup", rationale: "A deleted user has no metadata.", files: ["src/user_profile.py"],
  diff: "--- a/src/user_profile.py\n+++ b/src/user_profile.py\n@@ -1 +1 @@\n-    old()\n+    new()\n", status: "pending", drafted_by: "llm", model: "m1",
  tests: ["test the deleted user"], result: "", pr_url: "", created_at: new Date().toISOString(), checks: { syntax_ok: true, lines_changed: 2, notes: [] }, ...over,
});

function show(items: unknown[], canAct = true) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body as string | undefined });
    if (url.endsWith("/decision")) return json(fix({ status: "approved", pr_url: "https://github.com/a/b/pull/9" }));
    if (url.endsWith("/fix-proposals") && method === "POST") return json(fix(), 201);
    return json(items);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><FixProposals investigationId="inv1" canAct={canAct} /></FeedbackProvider></QueryClientProvider>);
  return calls;
}

describe("Suggested fix", () => {
  it("shows the diff and only opens a pull request after the person confirms", async () => {
    const calls = show([fix()]);
    expect(await screen.findByText("Guard the lookup")).toBeInTheDocument();
    expect(screen.getByLabelText("Proposed change")).toHaveTextContent("+ new()");
    await userEvent.click(screen.getByRole("button", { name: /approve and open draft pr/i }));
    expect(calls.some((c) => c.url.endsWith("/decision"))).toBe(false); // nothing yet: the dialog asks first
    await userEvent.click(await screen.findByRole("button", { name: "Open draft pull request" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/decision"))?.body).toContain("approve"));
  });

  it("rejects without a dialog and offers the patch download", async () => {
    const calls = show([fix()]);
    await userEvent.click(await screen.findByRole("button", { name: /reject/i }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/decision"))?.body).toContain("reject"));
    expect(screen.getByRole("button", { name: /download patch/i })).toBeInTheDocument();
  });

  it("asks for a draft on request, hides actions from viewers, and blocks approval of a fix that does not parse", async () => {
    const calls = show([]);
    await userEvent.click(await screen.findByRole("button", { name: /propose a fix/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/fix-proposals"))).toBe(true));
    vi.restoreAllMocks();
    show([fix({ checks: { syntax_ok: false, lines_changed: 2, notes: ["a.py would not parse"] } })]);
    expect(await screen.findByText("a.py would not parse")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /approve and open draft pr/i }).at(-1)).toBeDisabled();
  });

  it("gives viewers the diff but no way to decide", async () => {
    show([fix()], false);
    await screen.findByText("Guard the lookup");
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /propose a fix/i })).toBeNull();
  });
});
