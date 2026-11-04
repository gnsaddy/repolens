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
  { key: "security-auditor", name: "Security auditor", summary: "Reads code for ways it could be attacked.", placeholder: "The login endpoint.", modes: ["review", "design"], overview: { label: "Threat model", action: "Write the threat model", first: "Threat model", second: "Weak points and attack paths", placeholder: "Leave empty for the whole repository." } },
  { key: "test-engineer", name: "Test engineer", summary: "Reads code for behaviour that has no test.", placeholder: "The order total.", modes: ["review", "design"], overview: { label: "Test plan", action: "Write the test plan", first: "Test strategy", second: "Test plan by module", placeholder: "Leave empty for the whole repository." } },
  { key: "architect", name: "Architect", summary: "Reads how the code is structured.", placeholder: "The orders module.", modes: ["review", "design"], overview: { label: "Design overview (HLD and LLD)", action: "Write the design overview", first: "High-level design", second: "Low-level design", placeholder: "Leave empty for the whole repository." } },
  { key: "developer", name: "Developer", summary: "Reviews the code like a colleague.", placeholder: "create_order.", modes: ["review", "design"], overview: { label: "Code walkthrough", action: "Write the code walkthrough", first: "How it works", second: "Where to change what", placeholder: "Leave empty for the whole repository." } },
];
const tokens = { calls: 1, input_tokens: 1500, output_tokens: 400, cost: 0.0105 };
const short: PersonaRunShort = { id: "r1", persona: "security-auditor", persona_name: "Security auditor", title: "the user lookup", status: "done", repo: "shop", repo_id: "a", created_at: new Date().toISOString(), found: 1, high: 1, tokens, shared: false, mine: true, author_name: "sam" };
const full: PersonaRunView = {
  ...short, request: "the user lookup", summary: "find_user joins the name into SQL.", base_sha: "abcdef123456", model: "claude-sonnet-5-5", error: "",
  findings: [{ title: "SQL injection in find_user", severity: "high", file: "app/auth.py", line: 5, evidence: "cur = db.execute(\"SELECT * FROM users\")", explanation: "name is joined into the query.", suggestion: "Pass it as a parameter.", code: "db.execute('x', (name,))", code_ok: false }],
  notes: { coverage: [{ file: "app/auth.py", whole: true }, { file: "app/big.py", whole: false }], dropped: 2, reasons: ["a", "b"], limit: "Read by a model, not run. A finding quotes real code, but that does not make it correct; check it." },
};

const designRun: PersonaRunView = {
  ...full, persona: "architect", persona_name: "Architect", mode: "design", findings: [], summary: "A small service that looks users up.",
  notes: {
    mode: "design", coverage: [{ file: "app/auth.py", whole: true }, { file: "app/big.py", whole: false, outline: true }], main_files: ["app/auth.py", "app/big.py"], dropped: 1, reasons: ["x"], limit: "Read by a model, not run.",
    design: {
      hld: "## Purpose\n\nLooks users up in a database.", lld: "## auth\n\nfind_user builds a query.", diagram: "flowchart LR\n  A[auth] --> B[(db)]",
      components: [{ name: "auth", responsibility: "looks users up", files: ["app/auth.py"] }],
      flows: [{ name: "lookup", steps: ["find_user builds the query", "fetchone returns the row"], files: ["app/auth.py"] }],
      not_covered: ["the web layer was not shown"],
    },
  },
};

function open(opts: { design?: boolean; mainFiles?: boolean;  role?: string; fail?: string; reuse?: boolean; theirs?: boolean; empty?: boolean; architectNotes?: boolean } = {}) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/auth/me")) return json(me(opts.role));
    if (url.endsWith("/repositories")) return json(repos);
    if (url.endsWith("/personas")) return json({ personas });
    if (url.endsWith("/personas/runs") && method === "POST") return opts.fail ? json({ detail: opts.fail }, 409) : opts.design ? json(designRun, 201) : json({ ...full, reused: opts.reuse && !JSON.parse(String(init?.body)).fresh }, 201);
    if (url.endsWith("/personas/runs")) return json({ runs: [opts.theirs ? { ...short, mine: false, shared: true, author_name: "ann" } : short] });
    if (url.includes("/personas/runs/r1") && method === "DELETE") return json(null, 204);
    if (url.includes("/personas/runs/r1") && method === "PATCH") return json({ ...full, shared: JSON.parse(String(init?.body)).shared });
    if (url.includes("/personas/runs/r1") && opts.architectNotes) return json({ ...full, notes: { ...full.notes, dependencies: { hidden: 2, facts: [{ direction: "used_by", path: "app/auth.py", repo: "billing", repo_id: "b", from: "billing/invoice.py:total", to: "app/auth.py:find_user", kind: "import", trust: "fact", evidence: "total calls find_user" }] } } });
    if (url.includes("/personas/runs/r1") && opts.design) return json(designRun);
    if (url.includes("/personas/runs/r1") && opts.mainFiles) return json({ ...full, notes: { ...full.notes, main_files: ["app/auth.py", "app/main.py"] } });
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
    expect(within(group).getAllByRole("radio")).toHaveLength(4);
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

  it("shows an architect what it was told about other repositories, and counts what it cannot show", async () => {
    open({ architectNotes: true });
    await userEvent.click(await screen.findByRole("button", { name: /^the user lookup/ }));
    const card = await screen.findByLabelText("Dependencies");
    expect(card).toHaveTextContent("app/auth.py is used by billing");
    expect(card).toHaveTextContent("billing/invoice.py:total");
    expect(card).toHaveTextContent("2 more links involve repositories you cannot access; they are not described.");
  });

  it("offers the architect and the developer as personas to choose", async () => {
    const calls = open();
    const group = await screen.findByRole("group", { name: "Persona" });
    await userEvent.click(within(group).getByRole("radio", { name: /Architect/ }));
    await userEvent.type(screen.getByLabelText("What should it look at?"), "the orders module");
    await userEvent.click(screen.getByRole("button", { name: /Ask the architect/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/personas/runs") && c.body?.persona === "architect")).toBe(true));
    expect(within(group).getByRole("radio", { name: /Developer/ })).toBeInTheDocument();
  });

  it("lets a persona switch to its own overview, and a blank box asks for the whole repository", async () => {
    const calls = open({ design: true });
    const group = await screen.findByRole("group", { name: "Persona" });
    await userEvent.click(within(group).getByRole("radio", { name: /Architect/ }));
    await userEvent.click(within(await screen.findByRole("group", { name: "What the architect does" })).getByRole("radio", { name: /Design overview/ }));
    const go = screen.getByRole("button", { name: "Write the design overview" });
    expect(go).toBeEnabled();  // an empty box is fine here
    await userEvent.click(go);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/personas/runs") && c.body?.mode === "design" && c.body?.persona === "architect" && c.body?.request === "Describe the design of this repository")).toBe(true));
  });

  it("shows the design: high-level and low-level text, components and flows with their files, the diagram, and what it could not tell", async () => {
    open({ design: true });
    await userEvent.click(await screen.findByRole("button", { name: /^the user lookup/ }));
    expect(await screen.findByLabelText("High-level design")).toHaveTextContent("Looks users up in a database.");
    expect(screen.getByLabelText("Components")).toHaveTextContent("auth — looks users up");
    expect(screen.getByLabelText("Diagram")).toHaveTextContent("flowchart LR");
    const lld = screen.getByLabelText("Low-level design");
    expect(lld).toHaveTextContent("find_user builds a query.");
    expect(within(screen.getByLabelText("Flows")).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByLabelText("Not covered")).toHaveTextContent("the web layer was not shown");
    expect(screen.queryByLabelText("Findings")).toBeNull();
    expect(screen.getByLabelText("Coverage")).toHaveTextContent("its first lines and what it declares");
    expect(screen.getByLabelText("Coverage")).toHaveTextContent("1 part was left out");
  });

  it("says when nothing in the request matched and the main files were read instead", async () => {
    open({ mainFiles: true });
    await userEvent.click(await screen.findByRole("button", { name: /^the user lookup/ }));
    expect(await screen.findByRole("note")).toHaveTextContent("it read the repository's main files: app/auth.py, app/main.py");
  });

  it("gives each persona its own overview: a threat model, a test plan, a code walkthrough", async () => {
    open();
    const group = await screen.findByRole("group", { name: "Persona" });
    for (const [persona, label, action] of [["Security auditor", "Threat model", "Write the threat model"], ["Test engineer", "Test plan", "Write the test plan"], ["Developer", "Code walkthrough", "Write the code walkthrough"]] as const) {
      await userEvent.click(within(group).getByRole("radio", { name: new RegExp(persona) }));
      const picker = await screen.findByRole("group", { name: `What the ${persona.toLowerCase()} does` });
      await userEvent.click(within(picker).getByRole("radio", { name: label }));
      expect(screen.getByRole("button", { name: action })).toBeEnabled();  // an empty box is fine
    }
  });
});
