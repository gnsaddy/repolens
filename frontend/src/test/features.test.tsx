import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import type { FeatureDraft, FeatureDraftView } from "@/lib/api";
import FeaturesPage from "@/pages/Features";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const me = (role = "member") => ({ id: "1", email: "sam@acme.io", name: "Sam", role, org_id: "o", auth_source: "password" });
const repos = [{ id: "a", name: "shop", source: "git", status: "ready" }, { id: "h", name: "handbook", source: "docs", status: "ready" }, { id: "n", name: "new-repo", source: "git", status: "indexing" }];
const tokens = { calls: 1, input_tokens: 2000, output_tokens: 300, cost: 0.0105 };
const short: FeatureDraft = { id: "d1", title: "Add a discount", status: "drafted", repo: "shop", repo_id: "a", created_at: new Date().toISOString(), ok: true, tokens };
const full: FeatureDraftView = {
  ...short, request: "Add a discount", spec: "Add an optional percentage.", base_sha: "abcdef123456", model: "claude-sonnet-5-5", error: "",
  impact: [{ file: "app/pricing.py", dependants: 2, repos: ["billing"], hidden: 1, examples: ["billing: invoices.py:total"] }],
  diff: "+def compute_total(items, discount=0):", new_files: [{ path: "tests/test_discount.py", content: "x", test: true }],
  review: { ok: false, errors: 1, warnings: 1, note: "Read, not run: nothing was executed, so behaviour is not proved.",
    findings: [{ level: "error", file: "app/bad.py", message: "Would not parse: line 1" }, { level: "warn", file: "", message: "The draft adds no tests." }] },
  pr_title: "Add a discount", pr_body: "Adds an optional percentage.", note: "A draft is text only. Nothing was applied, branched or pushed. Copy it where you want it and test it.",
};

function open(opts: { role?: string; drafts?: FeatureDraft[]; fail?: string; reuse?: boolean } = {}) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/auth/me")) return json(me(opts.role));
    if (url.endsWith("/repositories")) return json(repos);
    if (url.endsWith("/features") && method === "POST") return opts.fail ? json({ detail: opts.fail }, 409) : json({ ...full, reused: opts.reuse && !JSON.parse(String(init?.body)).fresh }, 201);
    if (url.endsWith("/features")) return json({ drafts: opts.drafts ?? [short] });
    if (url.includes("/features/d1") && method === "DELETE") return json(null, 204);
    if (url.includes("/features/d1")) return json(full);
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><FeaturesPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("Build a feature", () => {
  it("offers only indexed code repositories, and sends the request for a draft", async () => {
    const calls = open();
    const select = await screen.findByLabelText("Repository");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(1));
    expect(within(select).getByRole("option", { name: "shop" })).toBeInTheDocument();
    const go = screen.getByRole("button", { name: /Draft the feature/ });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What should it do?"), "Add a discount to the order total");
    await userEvent.click(go);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/features") && c.body?.repo_id === "a" && c.body?.request === "Add a discount to the order total")).toBe(true));
    expect(await screen.findByLabelText("Tokens used")).toHaveTextContent("2.0k in, 300 out, 1 call, about $0.0105");
  });

  it("shows the impact, the review with its limits, the change and the pull request text", async () => {
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Add a discount/ }));
    const impact = await screen.findByLabelText("Impact");
    expect(impact).toHaveTextContent("app/pricing.py is used by 2 places in billing and 1 you cannot access");
    const review = screen.getByLabelText("Review");
    expect(review).toHaveTextContent("1 error");
    expect(review).toHaveTextContent("Would not parse: line 1");
    expect(review).toHaveTextContent("Read, not run");
    expect(screen.getByLabelText("Change")).toHaveTextContent("+def compute_total(items, discount=0):");
    expect(screen.getByLabelText("Change")).toHaveTextContent("tests/test_discount.py (test)");
    expect(screen.getByLabelText("Pull request text")).toHaveTextContent("Adds an optional percentage.");
    expect(screen.getByLabelText("Draft summary")).toHaveTextContent("Nothing was applied, branched or pushed");
  });

  it("says when an earlier draft was reused at no cost, and can ask the model again", async () => {
    const calls = open({ reuse: true });
    await userEvent.type(await screen.findByLabelText("What should it do?"), "Add a discount to the order total");
    await userEvent.click(screen.getByRole("button", { name: /Draft the feature/ }));
    expect(await screen.findByText(/no tokens were used/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Draft again anyway" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/features")).map((c) => c.body?.fresh)).toEqual([false, true]));
  });

  it("says why a draft could not be made", async () => {
    open({ fail: "Build a feature needs a language model. Add a provider and key under Settings first." });
    await userEvent.type(await screen.findByLabelText("What should it do?"), "Add a discount to the order total");
    await userEvent.click(screen.getByRole("button", { name: /Draft the feature/ }));
    expect(await screen.findByText(/needs a language model/)).toBeInTheDocument();
  });

  it("deletes a draft after asking", async () => {
    const calls = open();
    await userEvent.click(await screen.findByRole("button", { name: /Add a discount/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete draft" }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/features/d1"))).toBe(true));
  });

  it("lets a viewer read drafts but not make or delete them", async () => {
    open({ role: "viewer" });
    await userEvent.click(await screen.findByRole("button", { name: /Add a discount/ }));
    await screen.findByLabelText("Impact");
    expect(screen.queryByRole("button", { name: /Draft the feature/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete draft" })).toBeNull();
  });
});
