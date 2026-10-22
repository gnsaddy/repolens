import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import JiraPage from "@/pages/JiraPage";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const detail = { key: "BUG-7", summary: "Profile 500", description: "Happens when deleted", status: "Open", type: "Bug", priority: "High", labels: [], components: ["user-service"], reporter: "Ana", assignee: "", created: "", updated: "", url: "https://x/browse/BUG-7", investigation_id: null,
  comments: [{ author: "Ben", created: "", body: "Can you share the request?" }] };
const analysis = { key: "BUG-7", headline: "Profile 500 — signature: KeyError", status: "Open", priority: "High", reporter: "Ana", assignee: "", participants: ["Ben"], age_days: 5, updated_days: 1,
  signals: { errors: ["KeyError"], frames: [], files: ["src/user_profile.py"], symbols: [], pull_requests: [], related_issues: [] },
  discussion: { comments: 1, cause: [{ by: "Cy", text: "Root cause: soft-deleted rows", comment: 1 }], tried: [], workarounds: [], fixes: [], open_questions: [{ by: "Ben", text: "Can you share the request?", comment: 1 }] },
  timeline: [{ author: "Ben", created: "", kind: "question", excerpt: "Can you share the request?" }], flags: ["No reproduction steps are described."], search_terms: [], next_steps: ["Run an investigation."],
  related: [{ repo: "svc", repo_id: "r", file: "docs/a.md", kind: "doc", title: "Deleted users", start_line: 1, node_id: "n1" }] };

describe("Jira ticket panel", () => {
  it("opens a ticket, shows its comments, analyses it and starts an investigation", async () => {
    const posts: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method === "POST") posts.push(u);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/jira/connection")) return json({ base_url: "https://x.atlassian.net", deployment: "cloud", email: "", allow_publish: true, default_project: "" });
      if (u.includes("/jira/projects")) return json([]);
      if (u.includes("/jira/issues/BUG-7/analyze")) return json(analysis);
      if (u.includes("/jira/issues/BUG-7")) return json(detail);
      if (u.includes("/jira/issues")) return json([{ key: "BUG-7", summary: "Profile 500", status: "Open", type: "Bug", priority: "High", url: "", investigation_id: null }]);
      if (u.endsWith("/jira/investigate")) return json({ investigation_id: "inv1" });
      return json({});
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><JiraPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Profile 500" }));
    const dlg = await screen.findByRole("dialog");
    expect(await within(dlg).findByText("Can you share the request?")).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("tab", { name: "Analysis" }));
    expect(await within(dlg).findByText("Profile 500 — signature: KeyError")).toBeInTheDocument();
    expect(within(dlg).getByText("Root cause: soft-deleted rows")).toBeInTheDocument();
    expect(within(dlg).getByText("No reproduction steps are described.")).toBeInTheDocument();
    expect(within(dlg).getByRole("link", { name: "Deleted users" })).toHaveAttribute("href", "/knowledge?tab=graph&node=n1");
    await userEvent.click(within(dlg).getByRole("button", { name: /Investigate/ }));
    expect(posts.some((u) => u.endsWith("/jira/investigate"))).toBe(true);
  });
});
