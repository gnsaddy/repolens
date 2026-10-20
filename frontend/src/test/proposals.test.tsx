import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Wiki from "@/pages/Wiki";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const row = { id: "p1", kind: "update", source: "drift", source_ref: "a1", doc_repo_id: "d1", doc_repo: "handbook", doc_path: "billing.md", title: "Billing", rationale: "1 symbol(s) this page mentions changed: `compute_total`.", drafted_by: "template", status: "pending", result: "", created_at: new Date().toISOString(), decided_at: null };
const detail = { ...row, original: "# Billing\n", proposed: "# Billing\n\n> **Review needed**\n", diff: "--- a/billing.md\n+++ b/billing.md\n@@ -1 +1,3 @@\n # Billing\n+\n+> **Review needed**\n", apply_options: ["local"] };

describe("Wiki proposals", () => {
  it("shows the diff, lets you edit first, and applies only after confirmation", async () => {
    const calls: { url: string; method?: string; body?: unknown }[] = [];
    let status = "pending";
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method && init.method !== "GET") calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.includes("/wiki/overview")) return json({ sources: [], totals: { sources: 0, pages: 0, sections: 0 }, health: {}, concepts: 0 });
      if (u.endsWith("/wiki/drift/count")) return json({ open: 0 });
      if (u.endsWith("/decision")) { status = "applied"; return json({ ...detail, status, result: "Updated handbook/billing.md" }); }
      if (u.includes("/wiki/proposals/p1") && init?.method === "PUT") return json({ ...detail, drafted_by: "edited" });
      if (u.includes("/wiki/proposals/p1")) return json({ ...detail, status, result: status === "applied" ? "Updated handbook/billing.md" : "" });
      if (u.includes("/wiki/proposals")) return json(status === "pending" ? [row] : []);
      return json([]);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/wiki?tab=proposals&id=p1"]}><Wiki /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    const diff = await screen.findByLabelText("Proposed changes");
    expect(within(diff).getByText("+> **Review needed**")).toHaveClass("bg-emerald-100");
    expect(screen.getByText(/drafted by a template/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Edit first" }));
    const box = screen.getByLabelText("Edit proposed text");
    await userEvent.clear(box);
    await userEvent.type(box, "# Billing mine");
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && (c.body as { proposed: string }).proposed === "# Billing mine")).toBe(true));

    await userEvent.click(await screen.findByRole("button", { name: /Approve and apply/ }));
    expect(calls.some((c) => c.url.endsWith("/decision"))).toBe(false); // not before the confirmation
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/decision") && (c.body as { approve: boolean; target: string }).target === "local")).toBe(true));
    expect((await screen.findAllByText("Updated handbook/billing.md")).length).toBeGreaterThan(0);
  });

  it("offers a Confluence comment for synced pages and no write option for code repositories", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.includes("/wiki/overview")) return json({ sources: [], totals: { sources: 0, pages: 0, sections: 0 }, health: {}, concepts: 0 });
      if (u.endsWith("/wiki/drift/count")) return json({ open: 0 });
      if (u.includes("/wiki/proposals/p1")) return json({ ...detail, apply_options: ["confluence_comment"] });
      if (u.includes("/wiki/proposals/p2")) return json({ ...detail, id: "p2", apply_options: [] });
      return json([]);
    });
    const { unmount } = render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/wiki?tab=proposals&id=p1"]}><Wiki /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    expect(await screen.findByRole("button", { name: "Approve and comment in Confluence" })).toBeInTheDocument();
    unmount();
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/wiki?tab=proposals&id=p2"]}><Wiki /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    expect(await screen.findByText(/copy the proposed text into a pull request/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
  });
});
