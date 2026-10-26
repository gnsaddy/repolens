import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import SkillsPanel, { parseSkillMarkdown } from "@/components/SkillsPanel";
import type { SkillRow } from "@/lib/api";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const row = (o: Partial<SkillRow> = {}): SkillRow => ({ id: "s1", name: "api", title: "How we write API endpoints", description: "Use when writing endpoints", body: "Call authorize() first.", tasks: ["build"], repo_ids: [], repos: [], always: false, status: "approved", note: "", uses: 3, mine: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...o });

function open(items: SkillRow[], admin: boolean) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/repositories")) return json([{ id: "r1", name: "user-service", source: "local" }]);
    if (url.endsWith("/skills") && method === "POST") return json(row({ status: admin ? "approved" : "pending" }), 201);
    if (url.endsWith("/skills")) return json({ items, builtin: [{ name: "walkthrough", title: "Walkthrough for how and why questions", description: "d", applies: "explain" }], pending: items.filter((s) => s.status === "pending").length });
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><MemoryRouter><SkillsPanel admin={admin} canSubmit /></MemoryRouter></FeedbackProvider></QueryClientProvider>);
  return calls;
}

describe("Skills", () => {
  it("reads front matter, a heading or neither", () => {
    expect(parseSkillMarkdown("---\nname: api-style\ndescription: when writing endpoints\n---\n# API\nUse authorize().")).toEqual({ title: "api-style", description: "when writing endpoints", body: "# API\nUse authorize()." });
    expect(parseSkillMarkdown("# Runbook layout\nSteps.").title).toBe("Runbook layout");
    expect(parseSkillMarkdown("just text").title).toBe("");
  });

  it("lists skills with what they apply to, and the built-in ones", async () => {
    open([row(), row({ id: "s2", title: "Runbook layout", status: "pending", tasks: [], always: true })], true);
    const list = await screen.findByRole("list", { name: "Skill list" });
    expect(list).toHaveTextContent("How we write API endpoints");
    expect(list).toHaveTextContent("used 3 times");
    expect(list).toHaveTextContent("all repositories");
    expect(screen.getByRole("status")).toHaveTextContent("1 skill waiting for your review");
    expect(screen.getByText("Walkthrough for how and why questions")).toBeInTheDocument();
  });

  it("lets an admin approve or reject with a reason", async () => {
    const calls = open([row({ id: "p1", title: "Pending one", status: "pending" })], true);
    await userEvent.click(await screen.findByRole("button", { name: /Approve/ }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/skills/p1/review"))?.body).toEqual({ decision: "approve", note: "" }));
    await userEvent.click(screen.getByRole("button", { name: /Reject/ }));
    await userEvent.type(screen.getByLabelText("Reason for rejecting"), "too vague");
    await userEvent.click(screen.getByRole("button", { name: "Reject skill" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/review")).at(-1)?.body).toEqual({ decision: "reject", note: "too vague" }));
  });

  it("shows members no approval buttons and tells them an admin reviews submissions", async () => {
    open([row({ id: "m1", status: "pending", mine: true }), row({ id: "o1", title: "Someone else's", mine: false })], false);
    await screen.findByRole("list", { name: "Skill list" });
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(screen.getByText(/An admin reviews what you submit/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Edit/ })).toHaveLength(1); // only their own, still pending
  });

  it("submits a new skill and says whether it is active or waiting", async () => {
    const calls = open([], false);
    await userEvent.click(await screen.findByRole("button", { name: /Add a skill/ }));
    const dlg = await screen.findByRole("dialog");
    expect(dlg).toHaveTextContent("An admin reviews it before it is used");
    await userEvent.type(within(dlg).getByPlaceholderText(/How we write API endpoints/), "Runbook layout");
    await userEvent.type(within(dlg).getByLabelText("Instructions"), "Start with impact, then steps, then owners.");
    await userEvent.click(within(dlg).getByLabelText("Writing something"));
    await userEvent.click(within(dlg).getByRole("button", { name: "Submit for review" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/skills"))?.body).toMatchObject({ title: "Runbook layout", tasks: ["build"], repo_ids: [], always: false }));
  });

  it("fills the form from an uploaded SKILL.md and refuses other file types", async () => {
    open([], true);
    await userEvent.click(await screen.findByRole("button", { name: /Add a skill/ }));
    const dlg = await screen.findByRole("dialog");
    const input = within(dlg).getByLabelText("Upload a skill file") as HTMLInputElement;
    await userEvent.upload(input, new File(["---\nname: API style\ndescription: when writing endpoints\n---\nCall authorize() first."], "SKILL.md", { type: "text/markdown" }));
    await waitFor(() => expect(within(dlg).getByLabelText("Instructions")).toHaveValue("Call authorize() first."));
    expect(within(dlg).getByDisplayValue("API style")).toBeInTheDocument();
  });
});
