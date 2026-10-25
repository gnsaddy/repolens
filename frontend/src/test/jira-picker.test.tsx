import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Investigations from "@/pages/Investigations";

const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

function show(jira: unknown) {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const u = String(input);
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
    if (u.endsWith("/jira/connection")) return json(jira);
    if (u.endsWith("/repositories") || u.endsWith("/investigations")) return json([]);
    if (u.includes("/jira/issue-types") || u.includes("/jira/projects")) return json([]);
    if (u.includes("/jira/issues")) return json([{ key: "BUG-7", summary: "Profile 500", status: "Open", status_category: "To Do", type: "Bug", priority: "High", url: "", investigation_id: null }]);
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><Investigations /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
}

describe("Investigations: start from Jira", () => {
  it("offers the Jira search only when Jira is connected", async () => {
    show(null);
    await screen.findByRole("heading", { name: "Investigations" });
    expect(screen.queryByRole("button", { name: /Start from Jira/ })).not.toBeInTheDocument();
  });
  it("opens a ticket picker with analyse and investigate actions", async () => {
    show({ base_url: "https://x.atlassian.net", deployment: "cloud", email: "", allow_publish: false, default_project: "" });
    await userEvent.click(await screen.findByRole("button", { name: /Start from Jira/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.click(await within(dlg).findByRole("checkbox", { name: "Select BUG-7" }));
    expect(within(dlg).getByRole("button", { name: /Analyse together/ })).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: /Investigate it/ })).toBeInTheDocument();
  });
});
