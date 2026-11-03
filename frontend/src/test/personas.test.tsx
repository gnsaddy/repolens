import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import type { Persona, PersonaRunShort, PersonaRunView } from "@/lib/api";
import PersonasPage from "@/pages/Personas";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const me = (role = "member") => ({ id: "1", email: "sam@acme.io", name: "Sam", role, org_id: "o", auth_source: "password" });
const repos = [{ id: "a", name: "shop", source: "git", status: "ready" }, { id: "h", name: "handbook", source: "docs", status: "ready" }];
const personas: Persona[] = [
  { key: "security-auditor", name: "Security auditor", summary: "Reads code for ways it could be attacked.", placeholder: "The login endpoint." },
  { key: "test-engineer", name: "Test engineer", summary: "Reads code for behaviour that has no test.", placeholder: "The order total." },
];
const tokens = { calls: 1, input_tokens: 1500, output_tokens: 400, cost: 0.0105 };
const short: PersonaRunShort = { id: "r1", persona: "security-auditor", persona_name: "Security auditor", title: "the user lookup", status: "done", repo: "shop", repo_id: "a", created_at: new Date().toISOString(), found: 1, high: 1, tokens, shared: false, mine: true, author_name: "sam" };
const full: PersonaRunView = {
  ...short, request: "the user lookup", summary: "find_user joins the name into SQL.", base_sha: "abcdef123456", model: "claude-sonnet-5-5", error: "",
  findings: [{ title: "SQL injection in find_user", severity: "high", file: "app/auth.py", line: 5, evidence: "cur = db.execute(\"SELECT * FROM users\")", explanation: "name is joined into the query.", suggestion: "Pass it as a parameter.", code: "db.execute('x', (name,))", code_ok: false }],
  notes: { coverage: [{ file: "app/auth.py", whole: true }, { file: "app/big.py", whole: false }], dropped: 2, reasons: ["a", "b"], limit: "Read by a model, not run. A finding quotes real code, but that does not make it correct; check it." },
};

function open(opts: { role?: string; fail?: string; reuse?: boolean; theirs?: boolean; empty?: boolean } = {}) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/auth/me")) return json(me(opts.role));
    if (url.endsWith("/repositories")) return json(repos);
    if (url.endsWith("/personas")) return json({ personas });
    if (url.endsWith("/personas/runs") && method === "POST") return opts.fail ? json({ detail: opts.fail }, 409) : json({ ...full, reused: opts.reuse && !JSON.parse(String(init?.body)).fresh }, 201);
    if (url.endsWith("/personas/runs")) return json({ runs: [opts.theirs ? { ...short, mine: false, shared: true, author_name: "ann" } : short] });
    if (url.includes("/personas/runs/r1") && method === "DELETE") return json(null, 204);
    if (url.includes("/personas/runs/r1") && method === "PATCH") return json({ ...full, shared: JSON.parse(String(init?.body)).shared });
    if (url.includes("/personas/runs/r1")) return json(opts.empty ? { ...full, findings: [], notes: { ...full.notes, dropped: 0 } } : opts.theirs ? { ...full, mine: false, shared: true, author_name: "ann" } : full);
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><PersonasPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("Agent personas", () => {
  it("offers each persona, only indexed code repositories, and sends the chosen persona with the request", async () => {
    const calls = open();
    const group = await screen.findByRole("group", { name: "Persona" });
    expect(within(group).getAllByRole("radio")).toHaveLength(2);
    expect(within(await screen.findByLabelText("Repository")).getAllByRole("option")).toHaveLength(1);
    await userEvent.click(within(group).getByRole("radio", { name: /Test engineer/ }));
    const go = screen.getByRole("button", { name: /Ask the test engineer/ });
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What should it look at?"), "the order total");
    await userEvent.click(go);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/personas/runs") && c.body?.persona === "test-engineer" && c.body?.repo_id === "a" && c.body?.request === "the order total")).toBe(true));
    expect(await screen.findByLabelText("Tokens used")).toHaveTextContent("1.5k in, 400 out, 1 call, about $0.0105");
  });

  it("shows each finding with the code it quotes, where it is, and what the review saw and dropped", async () => {
    open();
    await userEvent.click(await screen.findByRole("button", { name: /^the user lookup/ }));
    const findings = await screen.findByLabelText("Findings");
    expect(findings).toHaveTextContent("SQL injection in find_user");
    expect(findings).toHaveTextContent("app/auth.py:5");
    expect(findings).toHaveTextContent("SELECT * FROM users");
    expect(findings).toHaveTextContent("does not parse, check it");
    const seen = screen.getByLabelText("Coverage");
    expect(seen).toHaveTextContent("app/auth.py whole file");
    expect(seen).toHaveTextContent("app/big.py only the parts around the match");
    expect(seen).toHaveTextContent("2 findings were dropped");
    expect(seen).toHaveTextContent("not run");
  });

  it("does not say a clean review means nothing is wrong", async () => {
    open({ empty: true });
    await userEvent.click(await screen.findByRole("button", { name: /^the user lookup/ }));
    expect(await screen.findByText(/does not mean there is nothing to find/)).toBeInTheDocument();
  });

  it("says why a review could not be made", async () => {
    open({ fail: "The security auditor needs a language model. Add a provider and key under Settings." });
    await userEvent.type(await screen.findByLabelText("What should it look at?"), "the login endpoint");
    await userEvent.click(screen.getByRole("button", { name: /Ask the security auditor/ }));
    expect(await screen.findByText(/needs a language model/)).toBeInTheDocument();
  });

  it("says when an earlier review was reused at no cost, and can ask again", async () => {
    const calls = open({ reuse: true });
    await userEvent.type(await screen.findByLabelText("What should it look at?"), "the user lookup");
    await userEvent.click(screen.getByRole("button", { name: /Ask the security auditor/ }));
    expect(await screen.findByText(/no tokens were used/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ask again anyway" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/personas/runs")).map((c) => c.body?.fresh)).toEqual([false, true]));
  });

  it("shares a review and makes it private again, and deletes it from the page or the list after asking", async () => {
    const calls = open();
    await userEvent.click(await screen.findByRole("button", { name: /^the user lookup/ }));
    expect(await screen.findByText("private to you")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Share with workspace/ }));
    expect(await screen.findByText("shared by you")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Make private/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").map((c) => c.body?.shared)).toEqual([true, false]));
    await userEvent.click(screen.getByRole("button", { name: "Delete the user lookup" }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/personas/runs/r1"))).toBe(true));
  });

  it("shows who shared a review and offers its reader no share or delete; a viewer cannot ask", async () => {
    open({ role: "viewer", theirs: true });
    const item = await screen.findByRole("button", { name: /^the user lookup/ });
    expect(item).toHaveTextContent("ann");
    expect(screen.queryByRole("button", { name: /Ask the/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete / })).toBeNull();
    await userEvent.click(item);
    await screen.findByLabelText("Findings");
    expect(screen.getByText("shared by ann")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Share with workspace|Make private/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete review" })).toBeNull();
  });
});
