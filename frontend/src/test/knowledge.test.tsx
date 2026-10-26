import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
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
  it("suggests similar earlier questions before the model's answer", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.endsWith("/knowledge/similar")) return json([{ id: "m1", asked: "How are deleted users handled?", similarity: 0.88, answer: "They get an Unknown profile. [S1]", sources: ["docs/arch.md"], age_hours: 3, hits: 2 }]);
      if (u.endsWith("/knowledge/ask")) return json({ query: "q", mode: "llm", answer: "Fresh AI answer.", sources: [], connections: [] });
      if (u.endsWith("/knowledge/search")) return json([]);
      return json({}, 404);
    });
    renderPage("/knowledge?tab=ask");
    await userEvent.type(await screen.findByLabelText("Question"), "what happens to deleted users");
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }));
    const prior = await screen.findByLabelText("Asked before");
    expect(prior).toHaveTextContent("How are deleted users handled?");
    expect(prior).toHaveTextContent("88% similar");
    expect(prior).toHaveTextContent("They get an Unknown profile");
    expect(await screen.findByText("Fresh AI answer.")).toBeInTheDocument();
  });

  it("lets people rate an answer; a thumbs-down asks what was wrong", async () => {
    const posts: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.endsWith("/feedback")) { posts.push(JSON.parse(String(init?.body))); return json({ id: "f1", rating: "down" }, 201); }
      if (u.endsWith("/knowledge/ask")) return json({ query: "how many retries", mode: "llm", answer: "Three times [S1].", sources: [], connections: [] });
      if (u.endsWith("/knowledge/search") || u.endsWith("/knowledge/similar")) return json([]);
      return json({}, 404);
    });
    renderPage("/knowledge?tab=ask");
    await userEvent.type(await screen.findByLabelText("Question"), "how many retries");
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }));
    await screen.findByText("Three times [S1].");
    await userEvent.click(screen.getByRole("button", { name: "Not helpful" }));
    await userEvent.type(screen.getByLabelText("What was wrong?"), "missed the backoff");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/Thanks, noted/)).toBeInTheDocument();
    expect(posts).toEqual([{ kind: "answer", rating: "down", target_id: "how many retries", query: "how many retries", note: "missed the backoff", meta: { from_memory: false } }]);
  });

  it("labels answers served from memory and can ask the model again", async () => {
    const bodies: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.endsWith("/knowledge/ask")) {
        const b = JSON.parse(String(init?.body));
        bodies.push(b);
        return json({ query: "q", mode: "llm", answer: b.fresh ? "Fresh answer." : "Remembered answer.", sources: [], connections: [], ...(b.fresh ? {} : { memory: { similarity: 0.97, asked: "earlier question", tokens_saved: 1234 } }) });
      }
      if (u.endsWith("/knowledge/search")) return json([]);
      return json({}, 404);
    });
    renderPage("/knowledge?tab=ask");
    await userEvent.type(await screen.findByLabelText("Question"), "How are deleted users handled?");
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }));
    expect(await screen.findByText(/From memory/)).toHaveTextContent("1,234 tokens");
    await userEvent.click(screen.getByRole("button", { name: "Answer again with the model" }));
    expect(await screen.findByText("Fresh answer.")).toBeInTheDocument();
    expect(screen.queryByText(/From memory/)).not.toBeInTheDocument();
    expect(bodies).toMatchObject([{ fresh: false }, { fresh: true }]);
  });

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
      if (u.includes("/graph/nodes/n1/brief")) return json({ summary: "Function get_profile in src/user_profile.py:9. Called by 2 place(s).", callers: [{ id: "c1", name: "handle", qualname: "handle_profile_request", type: "function", path: "src/api.py", repo: "svc" }], dependencies: [], documented_in: [], history: [], callers_total: 2, cross_repo: false, notes: ["Public and not described in any document."] });
      if (u.includes("/graph/nodes/n1")) return json({ node: nodes[0], content: "def get_profile(): pass", nodes, edges: [], connections: [{ rel: "MENTIONS", direction: "in", label: "mentioned in", provenance: "doc_mention", weight: 0.9, evidence: "", node: nodes[1] }] });
      return json({}, 404);
    });
    renderPage("/knowledge?tab=graph");
    const n = await screen.findByRole("button", { name: "function get_profile" });
    expect(screen.getByRole("button", { name: "section Deleted users" })).toBeInTheDocument();
    await userEvent.click(n);
    expect(await screen.findByText("mentioned in")).toBeInTheDocument();
    expect(screen.getByText("Connections (1)")).toBeInTheDocument();
    const brief = await screen.findByLabelText("Before you change this");
    expect(brief).toHaveTextContent("Called by 2 place(s)");
    expect(brief).toHaveTextContent("Public and not described in any document.");
    expect(brief).toHaveTextContent("Used by: handle_profile_request");
  });

  it("marks inferred links, can hide everything but facts, and colours nodes by area", async () => {
    const nodes = [{ ...node("n1", "function", "get_profile", 3), community: 0 }, { ...node("n2", "section", "Deleted users", 2), community: 1 }, { ...node("n3", "file", "api.py", 1), community: 0 }];
    const edges = [{ ...edge("e1", "n2", "n1", "MENTIONS"), provenance: "doc_mention", trust: "inferred" as const, weight: 0.7 }, { ...edge("e2", "n1", "n3"), trust: "fact" as const }];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.includes("/graph/stats")) return json({ nodes: {}, edges: [], total_nodes: 3, total_edges: 2 });
      if (u.includes("/graph/overview")) return json({ nodes, edges });
      return json({}, 404);
    });
    renderPage("/knowledge?tab=graph");
    await screen.findByRole("button", { name: "function get_profile" });
    const svg = () => screen.getByRole("img", { name: "Knowledge graph" });
    const dashes = () => [...svg().querySelectorAll("line")].map((l) => l.getAttribute("stroke-dasharray"));
    expect(dashes()).toContain("5 3"); // the inferred link is dashed
    expect(screen.getByText(/dashed/)).toBeInTheDocument(); // and the legend says what that means
    await userEvent.click(screen.getByLabelText(/Facts only/));
    expect(svg().querySelectorAll("line")).toHaveLength(1); // only the directly found link remains
    expect(dashes()).not.toContain("5 3");
    const fillOf = (name: string) => screen.getByRole("button", { name }).querySelector("circle")?.getAttribute("fill");
    await userEvent.selectOptions(screen.getByLabelText("Colour by"), "community");
    expect(fillOf("function get_profile")).toBe(fillOf("file api.py")); // same area, same colour
    expect(fillOf("function get_profile")).not.toBe(fillOf("section Deleted users")); // a different area
    const legend = screen.getByRole("group", { name: "Areas" }); // pick one area to look at on its own
    await userEvent.click(within(legend).getByRole("button", { name: "Area 2" }));
    const opacityOf = (name: string) => Number(screen.getByRole("button", { name }).getAttribute("opacity"));
    expect(opacityOf("section Deleted users")).toBe(1);
    expect(opacityOf("function get_profile")).toBeLessThan(0.2);
    await userEvent.click(within(legend).getByRole("button", { name: "All areas" }));
    expect(opacityOf("function get_profile")).toBe(1);
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
