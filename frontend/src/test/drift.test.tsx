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

const alert = { id: "a1", status: "open", repo_id: "r1", repo: "shop", doc_repo_id: "d1", doc_repo: "handbook", doc_path: "billing.md", doc_title: "Billing", symbols: [{ id: "n1", name: "compute_total", qualname: "shop.orders.compute_total", path: "shop/orders.py", type: "function" }], from_sha: "aaaa111111", to_sha: "bbbb222222", created_at: new Date().toISOString(), updated_at: new Date().toISOString() };

describe("Wiki drift tab", () => {
  it("lists pages that describe changed code, shows a badge, and dismisses after confirmation", async () => {
    let dismissed = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.includes("/wiki/overview")) return json({ sources: [], totals: { sources: 0, pages: 0, sections: 0 }, health: {}, concepts: 0 });
      if (u.endsWith("/wiki/drift/count")) return json({ open: dismissed ? 0 : 1 });
      if (u.includes("/wiki/drift/a1/dismiss") && init?.method === "POST") { dismissed = true; return json({ status: "dismissed" }); }
      if (u.includes("/wiki/drift")) return json(dismissed ? [] : [alert]);
      return json([]);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/wiki?tab=drift"]}><Wiki /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    expect(await screen.findByText("shop.orders.compute_total")).toBeInTheDocument();
    expect(within(screen.getByRole("tab", { name: /Drift/ })).getByText("1")).toBeInTheDocument();
    expect(screen.getByText(/shop aaaa111111…bbbb222222/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Mark reviewed" }));
    await waitFor(() => expect(dismissed).toBe(true));
    expect(await screen.findByText(/Nothing to review/)).toBeInTheDocument();
  });
});
