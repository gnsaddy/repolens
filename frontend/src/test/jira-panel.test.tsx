import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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
      if (u.includes("/jira/issue-types")) return json(["Bug", "Task", "Story"]);
      if (u.includes("/jira/issues")) return json([{ key: "BUG-7", summary: "Profile 500", status: "Open", status_category: "To Do", type: "Bug", priority: "High", assignee: "Ben Lee", updated: new Date().toISOString(), comments: 2, url: "", investigation_id: null }]);
      if (u.endsWith("/jira/investigate")) return json({ investigation_id: "inv1" });
      return json({});
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><JiraPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("button", { name: /BUG-7\s+Profile 500/ }));
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


describe("Jira: several tickets at once", () => {
  const rows = [
    { key: "BUG-7", summary: "Profile 500 for deleted users", status: "Open", status_category: "To Do", type: "Bug", priority: "High", assignee: "Ben Lee", updated: new Date().toISOString(), comments: 2, url: "", investigation_id: null },
    { key: "BUG-8", summary: "Profile 500 error deleted users", status: "In review", status_category: "In Progress", type: "Bug", priority: "Medium", assignee: "", updated: new Date().toISOString(), comments: 0, url: "", investigation_id: null },
    { key: "TASK-3", summary: "Invoice export paging", status: "Open", status_category: "To Do", type: "Task", priority: "Low", assignee: "Cy Park", updated: new Date().toISOString(), comments: 1, url: "", investigation_id: "inv9" }];
  const batch = { tickets: rows.slice(0, 2).map((r) => ({ ...analysis, key: r.key, summary: r.summary, type: r.type, url: "", investigation_id: null })), related: [],
    common: { errors: [{ name: "KeyError", keys: ["BUG-7", "BUG-8"] }], files: [{ name: "src/user_profile.py", keys: ["BUG-7", "BUG-8"] }], symbols: [], components: [], possible_duplicates: [{ keys: ["BUG-7", "BUG-8"], why: "similar summaries", similarity: 0.8 }], statuses: { Open: 2 }, priorities: { High: 2 }, attention: [] } };
  function setup() {
    const calls: { url: string; body?: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method === "POST") calls.push({ url: u, body: init.body ? JSON.parse(String(init.body)) : undefined });
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/jira/connection")) return json({ base_url: "https://x.atlassian.net", deployment: "cloud", email: "", allow_publish: true, default_project: "" });
      if (u.includes("/jira/projects")) return json([]);
      if (u.includes("/jira/issue-types")) return json(["Bug", "Task"]);
      if (u.includes("/jira/issues")) { calls.push({ url: u }); return json(rows); }
      if (u.endsWith("/jira/analyze-batch")) return json(batch);
      if (u.endsWith("/jira/investigate-batch")) return json({ started: [{ issue_key: "BUG-7", investigation_id: "i1" }, { issue_key: "BUG-8", investigation_id: "i2" }], existing: [] });
      return json({});
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><JiraPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    return calls;
  }
  it("lists every type by default with type, status, assignee and whether an RCA exists", async () => {
    const calls = setup();
    const list = await screen.findByRole("list", { name: "Tickets" });
    expect(within(list).getByText("Task")).toBeInTheDocument();  // not just bugs
    expect(within(list).getByText("In review")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "View RCA" })).toHaveAttribute("href", "/investigations/inv9");
    expect(within(list).getAllByText("no RCA")).toHaveLength(2);
    const first = decodeURIComponent(calls.find((c) => c.url.includes("/jira/issues"))!.url);
    expect(first).not.toContain("issuetype");  // no type filter until someone chooses one
    expect(first).toContain("ORDER BY updated DESC");
    await userEvent.selectOptions(screen.getByLabelText("Type"), "Task");
    await waitFor(() => expect(calls.some((c) => decodeURIComponent(c.url).includes('issuetype = "Task"'))).toBe(true));
  });
  it("analyses selected tickets together and shows what they share", async () => {
    const calls = setup();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select BUG-7" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select BUG-8" }));
    expect(screen.getByRole("toolbar", { name: "Selected tickets" })).toHaveTextContent("2 selected");
    await userEvent.click(screen.getByRole("button", { name: /Analyse together/ }));
    const panel = await screen.findByLabelText("Batch analysis");
    expect(within(panel).getByText("Possible duplicates")).toBeInTheDocument();
    expect(within(panel).getByText(/BUG-7 and BUG-8/)).toBeInTheDocument();
    expect(within(panel).getAllByText("KeyError").length).toBeGreaterThan(0);
    expect(calls.find((c) => c.url.endsWith("/jira/analyze-batch"))?.body).toEqual({ keys: ["BUG-7", "BUG-8"] });
  });
  it("starts an investigation for each selected ticket", async () => {
    const calls = setup();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select BUG-7" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select BUG-8" }));
    await userEvent.click(screen.getByRole("button", { name: /Investigate all 2/ }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/jira/investigate-batch"))?.body).toEqual({ keys: ["BUG-7", "BUG-8"], repo_ids: [] }));
  });
  it("limits a selection to ten tickets", async () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ ...rows[0], key: `BUG-${i + 20}`, summary: `Ticket ${i}` }));
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/jira/connection")) return json({ base_url: "https://x", deployment: "cloud", email: "", allow_publish: true, default_project: "" });
      if (u.includes("/jira/issues")) return json(many);
      return json([]);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><JiraPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select the first ten" }));
    expect(screen.getByRole("toolbar", { name: "Selected tickets" })).toHaveTextContent("10 selected");
    expect(screen.getByRole("checkbox", { name: "Select BUG-30" })).toBeDisabled();
  });
});
