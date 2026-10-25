import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import AuditLog from "@/pages/AuditLog";

const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const rows = [
  { id: "1", at: new Date().toISOString(), actor: "ana@acme.io", action: "collection.create", target: "docs", allowed: true, detail: { count: "1" } },
  { id: "2", at: new Date().toISOString(), actor: "bo@acme.io", action: "jira.publish", target: "REP-1", allowed: false, detail: { reason: "publishing is disabled for this Jira connection" } }];

function show(role: string) {
  const urls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const u = String(input);
    urls.push(u);
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role, org_id: "o" });
    if (u.includes("/audit/facets")) return json({ groups: ["collection", "jira"], users: [{ id: "u1", email: "ana@acme.io" }], denied: 1 });
    if (u.includes("/audit?")) return json({ total: 2, items: rows });
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><AuditLog /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return urls;
}

describe("Audit log", () => {
  it("shows who did what and highlights what was refused, with the reason", async () => {
    show("admin");
    const t = await screen.findByRole("table", { name: "Audit events" });
    expect(within(t).getByText("collection.create")).toBeInTheDocument();
    expect(within(t).getByText("allowed")).toBeInTheDocument();
    expect(within(t).getByText("refused")).toBeInTheDocument();
    expect(within(t).getByText("publishing is disabled for this Jira connection")).toBeInTheDocument();
    expect(screen.getByText(/1–2 of 2/)).toBeInTheDocument();
  });
  it("filters by outcome and area through the API", async () => {
    const urls = show("admin");
    await screen.findByRole("table", { name: "Audit events" });
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "no");
    await waitFor(() => expect(urls.some((u) => u.includes("allowed=no"))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Area"), "jira");
    await waitFor(() => expect(urls.some((u) => u.includes("action=jira") && u.includes("allowed=no"))).toBe(true));
  });
  it("is closed to members", async () => {
    const urls = show("member");
    expect(await screen.findByText(/Only workspace admins/)).toBeInTheDocument();
    expect(urls.some((u) => u.includes("/audit"))).toBe(false);
  });
});
