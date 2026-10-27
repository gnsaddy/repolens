import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import type { CodeGraph, GraphNode, SystemAnalysis, SystemGroup, SystemOverview } from "@/lib/api";
import GraphPage from "@/pages/Graph";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const me = (role = "member") => ({ id: "1", email: "sam@acme.io", name: "Sam", role, org_id: "o", auth_source: "password" });
const repos = [{ id: "a", name: "orders-svc", source: "git" }, { id: "b", name: "pricing-svc", source: "git" }, { id: "c", name: "handbook", source: "docs" }];
const risk = (commits: number, fixes: number) => ({ commits, lines: commits * 10, fixes, drift_open: 0 });
const analysis: SystemAnalysis = {
  window_days: 90, estimate_tokens: 1500,
  repos: [{ id: "a", name: "orders-svc", source: "git", status: "ready", files: 3, chunks: 9, risk: risk(1, 0), heat: risk(0.5, 0) }, { id: "b", name: "pricing-svc", source: "git", status: "ready", files: 2, chunks: 6, risk: risk(2, 1), heat: risk(1, 1) }],
  links: [{ src: "a", dst: "b", count: 3, strong: 2, http: 1, imports: 0, weak: 0, strength: 1.2, rels: { CALLS: 2, HTTP: 1 }, trust: "fact", examples: [{ rel: "CALLS", from: "orders-svc: orders/handlers.py", to: "pricing-svc: pricing/engine.py", evidence: "handle_order calls compute_total()" }] }],
  coupling: [{ a: "a", b: "b", tickets: 1, ticket_examples: ["ORD-7"], days: 2, score: 5 }],
};
const group: SystemGroup = { id: "g1", name: "Checkout", description: "", repo_ids: ["a", "b"], hidden: 0, created_by: "2", created_by_name: "ann", created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
const overview: SystemOverview = { id: "o1", content: "## What this system is made of\norders-svc and pricing-svc.", mode: "llm", model: "m", unsupported_names: ["ghost/vanished.py"], created_by: "2", created_by_name: "ann", created_at: new Date().toISOString(), repositories: 2, partial: false };

const node = (id: string, name: string, repo_id: string, repo: string, path: string, type: GraphNode["type"] = "function"): GraphNode =>
  ({ id, type, name, qualname: name, path, start_line: 3, end_line: 9, repo_id, repo, degree: 2, meta: {}, community: null });
const codeGraph: CodeGraph = {
  nodes: [node("n1", "fetch_quote", "a", "orders-svc", "orders/client.py"), node("n2", "quote", "b", "pricing-svc", "pricing/api.py"), node("f1", "client.py", "a", "orders-svc", "orders/client.py", "file"),
    node("n3", "checkout", "a", "orders-svc", "orders/checkout.py"), node("n4", "UserClient", "b", "pricing-svc", "userlib/core.py", "class")],
  edges: [{ id: "http:n1:n2", source: "n1", target: "n2", rel: "HTTP", provenance: "http_route", trust: "inferred", weight: 0.7, evidence: "fetch_quote uses the URL /pricing/quote/{order_id}, which GET /pricing/quote/{order_id} serves" },
    { id: "c1", source: "f1", target: "n1", rel: "CONTAINS", provenance: "static_analysis", trust: "fact", weight: 1, evidence: "" },
    { id: "uses:n3:n4", source: "n3", target: "n4", rel: "USES", provenance: "import", trust: "fact", weight: 1, evidence: "checkout calls UserClient, imported with `from userlib import UserClient`" }],
  available: { strong: 2, weak: 4 }, shown_weak: false, truncated: false,
};

function open(opts: { role?: string; groups?: SystemGroup[]; overviews?: SystemOverview[]; code?: CodeGraph } = {}) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/auth/me")) return json(me(opts.role));
    if (url.endsWith("/repositories")) return json(repos);
    if (url.includes("/groups/analysis")) return json(analysis);
    if (url.includes("/groups/code-graph")) return json(opts.code ?? codeGraph, 200);
    if (url.endsWith("/overviews")) return json({ overviews: opts.overviews ?? [], hidden: 0 });
    if (url.endsWith("/overview") && method === "POST") return json(overview, 201);
    if (url.endsWith("/groups") && method === "POST") return json({ ...group, id: "g2", name: "Mine", created_by_name: "sam" }, 201);
    if (url.endsWith("/groups")) return json({ groups: opts.groups ?? [], max_repos: 15 });
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><GraphPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("Graph: systems", () => {
  it("draws the picked repositories and what each link is made of", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const dep = await screen.findByRole("button", { name: "orders-svc depends on pricing-svc: 3 links, 0 by name only, fact" });
    await userEvent.click(dep);
    const details = screen.getByLabelText("Link details");
    expect(details).toHaveTextContent("orders-svc → pricing-svc");
    expect(details).toHaveTextContent("calls 2");
    expect(details).toHaveTextContent("orders/handlers.py");
    expect(details).toHaveTextContent("handle_order calls compute_total()");
    expect(within(screen.getByLabelText("Changes together")).getByText(/1 shared ticket \(ORD-7\)/)).toBeInTheDocument();
    const trouble = screen.getByLabelText("Where trouble concentrates");
    expect(within(trouble).getByText("pricing-svc").closest("tr")).toHaveTextContent("2");
  });

  it("draws the functions on both ends of a link and says what connects them", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Code dependencies");
    await userEvent.click(await within(card).findByRole("button", { name: "function fetch_quote" }));
    const sel = within(card).getByLabelText("Selected code");
    expect(sel).toHaveTextContent("orders-svc");
    expect(sel).toHaveTextContent("orders/client.py:3");
    expect(sel).toHaveTextContent("calls over HTTP");
    expect(sel).toHaveTextContent("pricing-svc: quote");
    expect(sel).toHaveTextContent("URL matches a route");
    expect(sel).toHaveTextContent("GET /pricing/quote/{order_id} serves");
    expect(card).toHaveTextContent("2 links across repositories");
  });

  it("shows the import line as the proof that one repository uses a class from another", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Code dependencies");
    await userEvent.click(await within(card).findByRole("button", { name: "class UserClient" }));
    const sel = within(card).getByLabelText("Selected code");
    expect(sel).toHaveTextContent("userlib/core.py:3");
    expect(sel).toHaveTextContent("uses (imported from)");
    expect(sel).toHaveTextContent("orders-svc: checkout");
    expect(sel).toHaveTextContent("proved by an import");
    expect(sel).toHaveTextContent("from userlib import UserClient");
  });

  it("keeps calls matched by name alone out of the picture until asked, and says why", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Code dependencies");
    const box = await within(card).findByRole("checkbox", { name: /4 calls matched by name only/ });
    expect(box).not.toBeChecked();
    await userEvent.click(box);
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).includes("code-graph") && String(u).includes("weak=true"))).toBe(true));
  });

  it("explains an empty code graph instead of drawing nothing", async () => {
    open({ code: { nodes: [], edges: [], available: { strong: 0, weak: 12 }, shown_weak: false, truncated: false } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Code dependencies");
    expect(await within(card).findByText(/No code-level link was found/)).toBeInTheDocument();
    expect(card).toHaveTextContent("12 calls match a name that exists in the other repository");
    expect(card).toHaveTextContent("usually coincidences");
  });

  it("says plainly when no link was found rather than drawing an empty picture", async () => {
    open();
    vi.mocked(fetch).mockImplementation(((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return json(me());
      if (url.endsWith("/repositories")) return json(repos);
      if (url.includes("/groups/analysis")) return json({ ...analysis, links: [], coupling: [] });
      if (url.includes("/groups/code-graph")) return json({ nodes: [], edges: [], available: { strong: 0, weak: 0 }, shown_weak: false, truncated: false });
      return json({ groups: [], max_repos: 15, overviews: [], hidden: 0 });
    }) as typeof fetch);
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    expect(await screen.findByText(/No links between these repositories were found/)).toBeInTheDocument();
  });

  it("saves the selection as a named system", async () => {
    const calls = open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    await userEvent.click(await screen.findByRole("button", { name: /Save as a system/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.type(within(dlg).getByLabelText("Name"), "Mine");
    await userEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/groups"))?.body).toEqual({ name: "Mine", description: "", repo_ids: ["a", "b"] }));
  });

  it("opens a saved system, shows who saved it and the overview with who created it", async () => {
    open({ groups: [group], overviews: [overview] });
    await userEvent.click(await screen.findByRole("button", { name: /Checkout/ }));
    expect(screen.getByLabelText("Saved systems")).toHaveTextContent("ann");
    const art = await screen.findByLabelText("Overview by ann");
    expect(art).toHaveTextContent("Created by ann");
    expect(art).toHaveTextContent("orders-svc and pricing-svc.");
    expect(art).toHaveTextContent("Check these names, they are not in the facts: ghost/vanished.py");
  });

  it("writes an overview on request and says what it costs first", async () => {
    const calls = open({ groups: [group] });
    await userEvent.click(await screen.findByRole("button", { name: /Checkout/ }));
    expect(await screen.findByText(/about 1\.5k tokens/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Create knowledge for these repositories/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/groups/g1/overview") && c.method === "POST")).toBe(true));
  });

  it("will not write an overview until a changed selection is saved, and offers to update the system", async () => {
    open({ groups: [group] });
    await userEvent.click(await screen.findByRole("button", { name: /Checkout/ }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /handbook/ }));
    expect(await screen.findByRole("button", { name: /Update “Checkout”/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create knowledge/ })).toBeDisabled();
  });

  it("lets a viewer look but not save or write", async () => {
    open({ role: "viewer", groups: [group] });
    await userEvent.click(await screen.findByRole("button", { name: /Checkout/ }));
    await screen.findByLabelText("Repository map");
    expect(screen.queryByRole("button", { name: /Save as a system/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Create knowledge/ })).toBeNull();
  });
});
