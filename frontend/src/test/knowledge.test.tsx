import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { layout } from "@/components/ForceGraph";
import { AuthProvider } from "@/lib/auth";
import type { GraphEdge, GraphNode } from "@/lib/api";
import Knowledge from "@/pages/Knowledge";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const node = (id: string, type: GraphNode["type"], name: string, degree = 1): GraphNode => ({ id, type, name, qualname: name, path: "a.py", start_line: 1, end_line: 5, repo_id: "r", repo: "svc", degree, meta: {} });
const edge = (id: string, s: string, t: string, rel = "CALLS"): GraphEdge => ({ id, source: s, target: t, rel, provenance: "static_analysis", weight: 1, evidence: "" });

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("layout", () => {
  it("places every node inside the canvas, separated and deterministic", () => {
    const ns = ["a", "b", "c", "d"].map((i) => node(i, "function", i));
    const es = [edge("1", "a", "b"), edge("2", "b", "c"), edge("3", "c", "d")];
    const p1 = layout(ns, es);
    const p2 = layout(ns, es);
    expect(p1).toEqual(p2);
    for (const n of ns) { expect(p1[n.id].x).toBeGreaterThan(-100); expect(p1[n.id].x).toBeLessThan(1000); expect(p1[n.id].y).toBeGreaterThan(-100); expect(p1[n.id].y).toBeLessThan(700); }
    const d = (a: string, b: string) => Math.hypot(p1[a].x - p1[b].x, p1[a].y - p1[b].y);
    expect(d("a", "b")).toBeGreaterThan(20);
    expect(d("a", "b")).toBeLessThan(d("a", "d") + 200); // connected nodes stay near each other
  });
  it("handles empty graphs", () => expect(layout([], [])).toEqual({}));
});

function renderPage(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  localStorage.setItem("repolens.token", "tok");
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={[url]}><Knowledge /></MemoryRouter></AuthProvider></QueryClientProvider>);
}
const me = { id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" };

describe("Knowledge page", () => {
  it("asks a question and shows the answer, sources and graph connections", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.endsWith("/knowledge/ask")) return json({ query: "q", mode: "extractive", answer: "Deleted users get an Unknown profile. [S1]", sources: [], connections: [{ from: "Deleted users", label: "mentions", to: "UserProfileService.get_profile", to_type: "function", provenance: "doc_mention" }] });
      if (u.endsWith("/knowledge/search")) return json([{ chunk_id: "c1", repo_id: "r", repo: "svc", file: "docs/arch.md", kind: "doc", chunk_type: "doc", title: "Deleted users", symbol: "Deleted users", start_line: 1, end_line: 4, content: "A deleted user gets a default profile.", score: 0.1, node_id: "n1", via_graph: null,
        connections: [{ rel: "MENTIONS", direction: "out", label: "mentions", provenance: "doc_mention", weight: 0.9, evidence: "", node: node("n2", "function", "UserProfileService.get_profile") }] }]);
      return json({}, 404);
    });
    renderPage("/knowledge?tab=ask");
    await userEvent.type(await screen.findByLabelText("Question"), "How are deleted users handled?");
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }));
    expect(await screen.findByText(/Unknown profile/)).toBeInTheDocument();
    expect(screen.getByText("extracted passages")).toBeInTheDocument();
    expect(await screen.findByText("docs/arch.md", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText("UserProfileService.get_profile").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /mentions UserProfileService.get_profile/ })).toBeInTheDocument();
  });

  it("renders the graph with accessible nodes and selects one", async () => {
    const nodes = [node("n1", "function", "get_profile", 3), node("n2", "section", "Deleted users", 2), node("n3", "file", "api.py", 1)];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.includes("/graph/stats")) return json({ nodes: { function: 1, section: 1, file: 1 }, edges: [{ rel: "MENTIONS", provenance: "doc_mention", count: 1 }], total_nodes: 3, total_edges: 2 });
      if (u.includes("/graph/overview")) return json({ nodes, edges: [edge("e1", "n2", "n1", "MENTIONS"), edge("e2", "n1", "n3")] });
      if (u.includes("/graph/nodes/n1")) return json({ node: nodes[0], content: "def get_profile(): pass", nodes, edges: [], connections: [{ rel: "MENTIONS", direction: "in", label: "mentioned in", provenance: "doc_mention", weight: 0.9, evidence: "", node: nodes[1] }] });
      return json({}, 404);
    });
    renderPage("/knowledge?tab=graph");
    const n = await screen.findByRole("button", { name: "function get_profile" });
    expect(screen.getByRole("button", { name: "section Deleted users" })).toBeInTheDocument();
    await userEvent.click(n);
    expect(await screen.findByText("mentioned in")).toBeInTheDocument();
    expect(screen.getByText("Connections (1)")).toBeInTheDocument();
  });

  it("uploads selected files to the collection", async () => {
    const calls: { url: string; body?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      calls.push({ url: u, body: init?.body as string | undefined });
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([{ id: "col1", name: "handbook", source: "docs", url: "", default_branch: "main", indexed_sha: null, status: "ready", file_count: 0, chunk_count: 0, updated_at: new Date().toISOString() }]);
      if (u.endsWith("/documents") && init?.method === "POST") return json({ received: 1, changed: 1 });
      if (u.endsWith("/documents")) return json([]);
      return json({}, 404);
    });
    renderPage("/knowledge?tab=docs");
    const input = await screen.findByLabelText("Upload documents");
    await userEvent.upload(input, new File(["# Hello\n\nworld"], "hello.md", { type: "text/markdown" }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith("/documents") && c.body)).toBe(true));
    const post = calls.find((c) => c.body)!;
    expect(JSON.parse(post.body!)).toEqual({ documents: [{ path: "hello.md", content: "# Hello\n\nworld" }] });
  });
});
