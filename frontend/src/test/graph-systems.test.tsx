import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import type { Architecture, AskResult, ChangePair, CodeGraph, GraphEdge, GraphNode, Impact, LinkChanges, Surface, SystemAnalysis, SystemGroup, SystemOverview } from "@/lib/api";
import GraphPage from "@/pages/Graph";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const me = (role = "member") => ({ id: "1", email: "sam@acme.io", name: "Sam", role, org_id: "o", auth_source: "password" });
const repos = [{ id: "a", name: "orders-svc", source: "git" }, { id: "b", name: "pricing-svc", source: "git" }, { id: "c", name: "handbook", source: "docs" }];
const risk = (commits: number, fixes: number) => ({ commits, lines: commits * 10, fixes, drift_open: 0 });
const analysis: SystemAnalysis = {
  window_days: 90, estimate_tokens: 1500,
  repos: [{ id: "a", name: "orders-svc", source: "git", status: "ready", files: 3, role: "application", chunks: 9, risk: risk(1, 0), heat: risk(0.5, 0) }, { id: "b", name: "pricing-svc", source: "git", status: "ready", files: 2, role: "infrastructure", chunks: 6, risk: risk(2, 1), heat: risk(1, 1) }],
  links: [{ src: "a", dst: "b", count: 3, strong: 2, http: 1, imports: 0, declared: 0, infra: 0, weak: 0, strength: 1.2, rels: { CALLS: 2, HTTP: 1 }, trust: "fact", examples: [{ rel: "CALLS", from: "orders-svc: orders/handlers.py", to: "pricing-svc: pricing/engine.py", evidence: "handle_order calls compute_total()" }] }],
  coupling: [{ a: "a", b: "b", tickets: 1, ticket_examples: ["ORD-7"], days: 2, score: 5 }],
  coverage: { a: { languages: { python: 4, go: 2 }, unread: [{ detector: "imports", language: "go", files: 2 }], provides: [], route_files: 0, detectors: ["imports", "urls"] }, b: { languages: { python: 3 }, unread: [], provides: ["pricing"], route_files: 1, detectors: ["imports", "urls"] } },
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

const impact: Impact = {
  node: { id: "n4", name: "UserClient", type: "class", path: "userlib/core.py", repo: "pricing-svc" },
  direct: [{ link: "l1", repo_id: "a", repo: "orders-svc", label: "orders/checkout.py:checkout", target: "userlib/core.py:UserClient", kind: "import", rel: "USES", trust: "fact", evidence: "checkout calls UserClient, imported with `from userlib import UserClient`", more: 2, since: new Date().toISOString(), owners: [{ name: "ann", commits: 7 }] }],
  indirect: [{ repo: "orders-svc", label: "orders/jobs.py:nightly", through: "orders/checkout.py:checkout" }],
  repos: [{ repo: "orders-svc", uses: 3 }], hidden: 1, note: "Only repositories RepoLens has indexed are searched.",
};
const surface: Surface = {
  repo: { id: "a", name: "orders-svc" },
  symbols: [{ id: "n4", name: "UserClient", type: "class", path: "userlib/core.py", uses: 5, repos: ["shop-app"], kinds: ["import"], trust: "fact" }],
  dependants: ["shop-app"], declared_by: [{ repo: "shop-app", file: "pyproject.toml:user-lib", evidence: "pyproject.toml:3 requires user-lib" }], linked_by: [{ repo: "platform-infra", rel: "DEPLOYS", file: "k8s/a.yaml:ghcr.io/acme/payments", evidence: "uses the image" }], hidden: 0, exports: 3, unused_exports: [{ name: "unused_helper", package: "userlib", path: "userlib/core.py" }], note: "Counts the repositories RepoLens has indexed.",
};
const pairOf = (n: number): ChangePair => ({ src: "a", dst: "b", from: "orders-svc", to: "pricing-svc", count: n, examples: [{ from: "orders/reports.py:report", to: "userlib/core.py:unused_helper", evidence: "report calls unused_helper, imported with `from userlib import unused_helper`", at: new Date().toISOString() }] });
const changes: LinkChanges = { days: 7, tracking_since: new Date(Date.now() - 86400000).toISOString(), new: [pairOf(2)], removed: [pairOf(1)], computed_at: new Date().toISOString() };

const architecture: Architecture = {
  checked: 2, hidden_rules: 1, broken: 1,
  rules: [{ id: "r1", kind: "forbid", src: "a", dst: "b", src_name: "orders-svc", dst_name: "pricing-svc", note: "orders stays below pricing", created_by: "2", created_at: new Date().toISOString(), broken: true,
    violation: { links: 2, trust: "fact", rels: { USES: 2 }, examples: [{ rel: "USES", from: "orders-svc: orders/checkout.py", to: "pricing-svc: pricing/core.py", evidence: "checkout calls quote, imported with `from pricing import quote`" }] } }],
  cycles: [{ repos: ["a", "b"], names: ["orders-svc", "pricing-svc"], edges: [{ src: "a", dst: "b", trust: "fact", links: 2, proof: "checkout imports quote" }, { src: "b", dst: "a", trust: "fact", links: 1, proof: "quote imports Order" }] }],
};

const askResult: AskResult = {
  query: "How does orders-svc depend on pricing-svc?", answer: "orders-svc calls the pricing handler over HTTP [S1].", mode: "llm", unsupported_names: ["ghost.py"],
  sources: [
    { id: "S1", repo: "dependency analysis", file: "The link from orders-svc to pricing-svc", kind: "analysis", title: "The link", start_line: 0, end_line: 0, node_id: "n1", snippet: "", via_graph: null },
    { id: "S2", repo: "orders-svc", file: "orders/client.py", kind: "code", title: "fetch_quote", start_line: 3, end_line: 9, node_id: null, snippet: "", via_graph: null }],
  connections: [],
};

function open(opts: { role?: string; groups?: SystemGroup[]; overviews?: SystemOverview[]; code?: CodeGraph; changes?: LinkChanges; ask?: AskResult; analysis?: SystemAnalysis; architecture?: Architecture } = {}) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/auth/me")) return json(me(opts.role));
    if (url.endsWith("/repositories")) return json(repos);
    if (url.includes("/groups/analysis")) return json(opts.analysis ?? analysis);
    if (url.endsWith("/groups/ask")) return json(opts.ask ?? askResult);
    if (url.includes("/groups/impact")) return json(impact);
    if (url.includes("/groups/surface")) return json(surface);
    if (url.includes("/groups/changes")) return json(opts.changes ?? changes);
    if (url.includes("/groups/code-graph")) return json(opts.code ?? codeGraph, 200);
    if (url.endsWith("/architecture")) return json(opts.architecture ?? architecture);
    if (url.includes("/groups/export")) return json({ format: "mermaid", filename: "x.mmd", content: "graph LR" });
    if (url.includes("/rules") && method === "POST") return json({ id: "r2" }, 201);
    if (url.includes("/rules/") && method === "DELETE") return json(null, 204);
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

  it("shows what depends on a selected class: where, who to ask, and what is further in", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Code dependencies");
    await userEvent.click(await within(card).findByRole("button", { name: "class UserClient" }));
    const blast = await within(card).findByLabelText("Blast radius");
    expect(blast).toHaveTextContent("3 places in 1 other repository depend on this");
    expect(blast).toHaveTextContent("orders-svc: orders/checkout.py:checkout");
    expect(blast).toHaveTextContent("ask ann (7)");
    expect(blast).toHaveTextContent("+2 more");
    expect(blast).toHaveTextContent("orders/jobs.py:nightly");
    expect(blast).toHaveTextContent("1 more in repositories you cannot access");
  });

  it("lists new and removed dependencies with the proof, and says tracking has a start", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("What changed");
    expect(card).toHaveTextContent("+ orders-svc → pricing-svc");
    expect(card).toHaveTextContent("2 links");
    expect(card).toHaveTextContent("− orders-svc → pricing-svc");
    expect(card).toHaveTextContent("from userlib import unused_helper");
    expect(card).toHaveTextContent("tracking since");
  });

  it("says plainly that nothing changed rather than showing an empty box", async () => {
    open({ changes: { ...changes, new: [], removed: [] } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    expect(await screen.findByLabelText("What changed")).toHaveTextContent("appeared or went away in the last 7 days");
  });

  it("shows what other repositories use from one repository, and its unused exports", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Public surface");
    expect(await within(card).findByText("UserClient")).toBeInTheDocument();
    expect(card).toHaveTextContent("5×");
    expect(card).toHaveTextContent("shop-app");
    expect(card).toHaveTextContent("not used by any repository here: unused_helper");
  });

  it("says which languages could not be read, so an empty graph is not mistaken for no dependencies", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Code dependencies");
    expect(await within(card).findByRole("note")).toHaveTextContent("Not read: go in orders-svc (2 files)");
  });

  it("answers a question about the chosen repositories and shows numbered sources, marking the analysis", async () => {
    const calls = open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Ask about these repositories");
    await userEvent.type(within(card).getByLabelText("Your question"), "How does orders call pricing?");
    await userEvent.click(within(card).getByRole("button", { name: "Ask" }));
    const ans = await within(card).findByLabelText("Answer");
    expect(ans).toHaveTextContent("orders-svc calls the pricing handler over HTTP [S1].");
    expect(ans).toHaveTextContent("Check these names, they are not in the sources: ghost.py");
    expect(ans).toHaveTextContent("dependency analysis");
    expect(ans).toHaveTextContent("orders/client.py");
    expect(calls.find((c) => c.url.endsWith("/groups/ask"))?.body).toEqual({ repo_ids: ["a", "b"], question: "How does orders call pricing?" });
  });

  it("says the answer is plain passages when no model is configured, and says when the model failed", async () => {
    open({ ask: { ...askResult, mode: "extractive", unsupported_names: [] } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Ask about these repositories");
    await userEvent.click(within(card).getByRole("button", { name: /fit together/ }));
    expect(await within(card).findByText(/No language model is configured/)).toBeInTheDocument();
  });

  it("offers questions drawn from the links it found, and asks one with a click", async () => {
    const calls = open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Ask about these repositories");
    await userEvent.click(await within(card).findByRole("button", { name: "How does orders-svc depend on pricing-svc?" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/groups/ask"))?.body).toEqual({ repo_ids: ["a", "b"], question: "How does orders-svc depend on pricing-svc?" }));
  });

  it("explains a selected link without typing a question", async () => {
    const calls = open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    await userEvent.click(await screen.findByRole("button", { name: "orders-svc depends on pricing-svc: 3 links, 0 by name only, fact" }));
    await userEvent.click(await screen.findByRole("button", { name: /Explain this link/ }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/groups/ask"))?.body).toMatchObject({ src: "a", dst: "b", question: "" }));
  });

  it("explains what depends on a selected class, and a cited source can be shown in the graph", async () => {
    const calls = open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const dep = await screen.findByLabelText("Code dependencies");
    await userEvent.click(await within(dep).findByRole("button", { name: "class UserClient" }));
    await userEvent.click(await within(dep).findByRole("button", { name: /Explain what depends on this/ }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/groups/ask"))?.body).toMatchObject({ node_id: "n4" }));
    const ask = await screen.findByLabelText("Ask about these repositories");
    await userEvent.click(await within(ask).findByRole("button", { name: "show in graph" }));
    expect(await within(dep).findByLabelText("Selected code")).toHaveTextContent("fetch_quote");
  });

  it("says when a declared dependency is not used and when a used one is not declared", async () => {
    const link = analysis.links[0];
    open({ analysis: { ...analysis, links: [{ ...link, declared: 1, strong: 0, http: 0, imports: 0, declared_unused: true, undeclared: false }] } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    await userEvent.click(await screen.findByRole("button", { name: /orders-svc depends on pricing-svc: 1 links/ }));
    expect(await screen.findByText(/Declared in a manifest, but nothing was found using it/)).toBeInTheDocument();
  });

  it("flags a dependency that is used in the code but missing from the manifest", async () => {
    const link = analysis.links[0];
    open({ analysis: { ...analysis, links: [{ ...link, declared: 0, imports: 2, declared_unused: false, undeclared: true }] } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    await userEvent.click(await screen.findByRole("button", { name: /orders-svc depends on pricing-svc: 5 links/ }));
    expect(await screen.findByText(/Used in the code, but not declared in its manifest/)).toBeInTheDocument();
  });

  it("names who declares the package in the public surface", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    expect(await screen.findByLabelText("Public surface")).toHaveTextContent("Declared as a dependency by: shop-app (pyproject.toml:user-lib)");
  });

  it("shows which repositories are infrastructure and counts shared names as links", async () => {
    const link = analysis.links[0];
    open({ analysis: { ...analysis, links: [{ ...link, strong: 0, http: 0, imports: 0, infra: 2, rels: { DEPLOYS: 1, REFERENCES: 1 } }] } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    expect(await screen.findByRole("button", { name: /orders-svc depends on pricing-svc: 2 links/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Repository pricing-svc" })).toHaveTextContent("infrastructure");
    expect(screen.getByRole("button", { name: "Repository orders-svc" })).not.toHaveTextContent("infrastructure");
  });

  it("explains a link made by a shared name in words, and says a variable name is a guess", async () => {
    const edges: GraphEdge[] = [
      { id: "image:n1:n2", source: "n1", target: "n2", rel: "DEPLOYS", provenance: "name", trust: "fact", weight: 1, evidence: "k8s/a.yaml:3 (a manifest image) uses the image ghcr.io/acme/payments; build.yml:9 (a CI build or push step) builds that image" },
      { id: "env:n3:n4", source: "n3", target: "n4", rel: "CONFIGURED", provenance: "name", trust: "uncertain", weight: 0.4, evidence: "app.py:2 (code that reads the variable) reads DB_URL; a.yaml:8 sets it" },
    ];
    open({ code: { ...codeGraph, edges: [...codeGraph.edges, ...edges] } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const card = await screen.findByLabelText("Code dependencies");
    await userEvent.click(await within(card).findByRole("button", { name: "function checkout" }));
    const guess = within(card).getByLabelText("Selected code");
    expect(guess).toHaveTextContent("is configured by");
    expect(guess).toHaveTextContent("the same variable name (a guess)");
    expect(guess).toHaveTextContent("reads DB_URL");
    await userEvent.click(within(card).getByRole("button", { name: "function fetch_quote" }));
    const deploy = within(card).getByLabelText("Selected code");
    expect(deploy).toHaveTextContent("deploys");
    expect(deploy).toHaveTextContent("the same image name");
    expect(deploy).toHaveTextContent("builds that image");
  });

  it("names who points at a repository by name in its public surface", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    expect(await screen.findByLabelText("Public surface")).toHaveTextContent("Pointed at by name from: platform-infra (deploys)");
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
      if (url.includes("/groups/changes")) return json({ ...changes, new: [], removed: [] });
      if (url.includes("/groups/surface")) return json(surface);
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
  it("shows the architecture rules of a saved system with what breaks them, the loops, and the hidden ones counted", async () => {
    open({ groups: [group] });
    await userEvent.click(await screen.findByRole("button", { name: /Checkout/ }));
    const card = await screen.findByLabelText("Architecture rules");
    await waitFor(() => expect(card).toHaveTextContent("orders-svc must not depend on pricing-svc"));
    expect(card).toHaveTextContent("broken");
    expect(card).toHaveTextContent("checkout calls quote, imported with `from pricing import quote`");
    expect(card).toHaveTextContent("1 broken, 1 loop");
    expect(card).toHaveTextContent("orders-svc ⇄ pricing-svc");
    expect(card).toHaveTextContent("1 more rule about repositories you cannot access");
  });

  it("adds and removes a rule, but only for someone who can write", async () => {
    const calls = open({ groups: [group] });
    await userEvent.click(await screen.findByRole("button", { name: /Checkout/ }));
    const card = await screen.findByLabelText("Architecture rules");
    await userEvent.selectOptions(await within(card).findByLabelText("Repository that must not depend"), "b");
    await userEvent.selectOptions(within(card).getByLabelText("Repository it must not depend on"), "a");
    await userEvent.click(within(card).getByRole("button", { name: /Add rule/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/groups/g1/rules") && c.body?.src === "b" && c.body?.dst === "a")).toBe(true));
    await userEvent.click(within(card).getByRole("button", { name: "Remove rule orders-svc to pricing-svc" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/rules/r1"))).toBe(true));
  });

  it("offers no rule editing to a viewer", async () => {
    open({ role: "viewer", groups: [group] });
    await userEvent.click(await screen.findByRole("button", { name: /Checkout/ }));
    const card = await screen.findByLabelText("Architecture rules");
    await within(card).findByText(/must not depend on pricing-svc/);
    expect(within(card).queryByRole("button", { name: /Add rule/ })).toBeNull();
    expect(within(card).queryByRole("button", { name: /Remove rule/ })).toBeNull();
  });

  it("exports the map as Mermaid", async () => {
    open();
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const create = vi.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    await userEvent.click(await screen.findByRole("button", { name: /Mermaid/ }));
    await waitFor(() => expect(create).toHaveBeenCalled());
  });
  it("colours the code graph by recent changes on request, saying every function takes its file's heat", async () => {
    open({ code: { ...codeGraph, heat: { n1: 1, n2: 0.2, f1: 1, n3: 0, n4: 0 }, heat_days: 90 } });
    await userEvent.click(await screen.findByLabelText("orders-svc"));
    await userEvent.click(screen.getByLabelText("pricing-svc"));
    const box = await screen.findByRole("checkbox", { name: /Colour by recent changes/ });
    expect(box).not.toBeChecked();
    expect(box.closest("label")).toHaveTextContent("last 90 days; every function takes its file's heat");
    await userEvent.click(box);
    expect(box).toBeChecked();
  });

});
