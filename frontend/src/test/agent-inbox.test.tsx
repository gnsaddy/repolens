import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentInbox from "@/components/AgentInbox";
import { FeedbackProvider } from "@/components/feedback";
import type { AgentDraft } from "@/lib/api";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
afterEach(() => vi.restoreAllMocks());

const comment: AgentDraft = {
  id: "d1", kind: "pr_comment", title: "PR #7: Fix the user lookup", body: "### RepoLens review of #7\n\nNo findings.", status: "new", error: "", posted_url: "",
  repo: "shop", link: "https://github.com/acme/shop/pull/7", created_at: new Date().toISOString(), investigations: [], reviews: [], files: ["app/auth.py"],
};
const ci: AgentDraft = {
  id: "d2", kind: "investigation", title: "CI failed on main", body: "", status: "read", error: "", posted_url: "", repo: "shop", link: "https://github.com/acme/shop/actions/runs/501",
  created_at: new Date().toISOString(), reviews: [], files: [],
  investigations: [{ id: "i1", title: "CI failure: x", status: "completed", ai: true, models: ["claude-sonnet-5-5"], tokens: 10 }],
};

function open(drafts: AgentDraft[], fail?: { status: number; detail: string }) {
  const calls: { method: string; url: string; body: Record<string, unknown> | null }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    const method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ method, url: u, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (method === "POST" && fail) return json({ detail: fail.detail }, fail.status);
    if (method !== "GET") return json({ ok: true });
    if (u.endsWith("/automations/inbox")) return json({ drafts });
    return json({}, 404);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><MemoryRouter><AgentInbox /></MemoryRouter></FeedbackProvider></QueryClientProvider>);
  return calls;
}

describe("agent inbox", () => {
  it("says nothing goes to GitHub by itself and shows what is waiting", async () => {
    open([comment, ci]);
    const box = await screen.findByLabelText("Inbox");
    await within(box).findByText("PR #7: Fix the user lookup");
    expect(box).toHaveTextContent("Nothing the agent writes goes to GitHub by itself");
    expect(box).toHaveTextContent("2 waiting");
    expect(within(box).getByRole("link", { name: "Investigation 1" })).toHaveAttribute("href", "/investigations/i1");
    expect(box).toHaveTextContent("AI written · claude-sonnet-5-5");
    expect(within(box).getByText("new")).toBeInTheDocument();
  });

  it("is empty and says so", async () => {
    open([]);
    expect(await screen.findByText("Nothing is waiting for you.")).toBeInTheDocument();
  });

  it("posts a comment only after a confirmation, with the text as edited", async () => {
    const calls = open([comment]);
    await screen.findByText("PR #7: Fix the user lookup");
    await userEvent.click(screen.getByText("Read and edit the comment before it is posted"));
    const box = screen.getByLabelText("Comment for PR #7: Fix the user lookup");
    await userEvent.clear(box);
    await userEvent.type(box, "Only this point.");
    await userEvent.click(screen.getByRole("button", { name: "Post to the pull request" }));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0); // asked first
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("Everyone who can see the pull request will see it");
    await userEvent.click(screen.getByRole("button", { name: "Post comment" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    expect(calls[0].url).toMatch(/\/automations\/inbox\/d1\/approve$/);
    expect(calls[0].body).toEqual({ body: "Only this point." });
  });

  it("does not post when the confirmation is cancelled", async () => {
    const calls = open([comment]);
    await screen.findByText("PR #7: Fix the user lookup");
    await userEvent.click(screen.getByRole("button", { name: "Post to the pull request" }));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(calls).toHaveLength(0);
  });

  it("shows GitHub's refusal and keeps the draft", async () => {
    open([comment], { status: 502, detail: "GitHub refused (403). The saved token needs write access to this repository." });
    await screen.findByText("PR #7: Fix the user lookup");
    await userEvent.click(screen.getByRole("button", { name: "Post to the pull request" }));
    await userEvent.click(await screen.findByRole("button", { name: "Post comment" }));
    expect(await screen.findByText(/needs write access/)).toBeInTheDocument();
    expect(screen.getByText("PR #7: Fix the user lookup")).toBeInTheDocument();
  });

  it("marks a draft read and dismisses it, and a CI pointer has nothing to post", async () => {
    const calls = open([comment, ci]);
    const box = await screen.findByLabelText("Inbox");
    await within(box).findByText("CI failed on main");
    const rows = within(box).getAllByRole("listitem").filter((li) => li.textContent?.includes("CI failed on main") && li.querySelector("button"));
    expect(within(rows[0]).queryByRole("button", { name: "Post to the pull request" })).toBeNull();
    await userEvent.click(within(box).getByRole("button", { name: "Mark read" }));
    await userEvent.click(within(box).getAllByRole("button", { name: "Dismiss" })[0]);
    await waitFor(() => expect(calls.map((c) => c.method)).toEqual(["PATCH", "PATCH"]));
    expect(calls[0].body).toEqual({ status: "read" });
    expect(calls[1].body).toEqual({ status: "dismissed" });
  });

  it("shows a comment that waits for its investigation, and cannot be posted yet", async () => {
    open([{ ...comment, id: "d3", title: "CI failure on pull request #7", body: "", pending: true }]);
    await screen.findByText("CI failure on pull request #7");
    expect(screen.getByText(/Waiting for the investigation to finish/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Post to the pull request" })).toBeDisabled();
  });

  it("shows a drafted page update with a link to the wiki, and the nightly watch as a list", async () => {
    open([
      { ...ci, id: "d4", kind: "wiki_proposal", title: "Stale page: Billing", body: "A drafted update for `billing.md` waits in the wiki proposals.", investigations: [] },
      { ...ci, id: "d5", kind: "watch", title: "shop: 2 page(s) out of date for over 14 days", body: "- `billing.md` since 2026-10-01", investigations: [] },
    ]);
    const box = await screen.findByLabelText("Inbox");
    await within(box).findByText("Stale page: Billing");
    expect(within(box).getByRole("link", { name: "Open the wiki" })).toHaveAttribute("href", "/wiki");
    expect(box).toHaveTextContent("drafted page update");
    expect(box).toHaveTextContent("- `billing.md` since 2026-10-01");
    expect(within(box).queryByRole("button", { name: "Post to the pull request" })).toBeNull();
  });
});
