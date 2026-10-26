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
  it("says which names were not found and what the sources do not mention", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.endsWith("/knowledge/similar")) return json([]);
      if (u.endsWith("/knowledge/ask")) return json({ query: "q", mode: "llm", answer: "It calls ghost(). [S1]", sources: [], connections: [], unsupported_names: ["ghost()", "services/ghost.py"], retrieval: { coverage: 0.33, missing: ["assign", "role"], refined: true } });
      if (u.endsWith("/knowledge/search")) return json([]);
      return json({}, 404);
    });
    renderPage("/knowledge?tab=ask");
    await userEvent.type(await screen.findByLabelText("Question"), "how are roles assigned");
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }));
    expect(await screen.findByText(/Check these names/)).toHaveTextContent("ghost(), services/ghost.py");
    expect(screen.getByText(/Your sources do not mention/)).toHaveTextContent("assign, role");
  });

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

  it("gives the graph the whole screen with a details card that can be hidden, and Escape brings the page back", async () => {
    const nodes = [node("n1", "function", "get_profile", 3), node("n2", "file", "api.py", 1)];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me);
      if (u.endsWith("/repositories")) return json([]);
      if (u.includes("/graph/stats")) return json({ nodes: { function: 1, file: 1 }, edges: [], total_nodes: 2, total_edges: 1 });
      if (u.includes("/graph/overview")) return json({ nodes, edges: [edge("e1", "n1", "n2")] });
      return json({}, 404);
    });
    renderPage("/knowledge?tab=graph");
    await screen.findByRole("button", { name: "function get_profile" });
    expect(screen.getByText("Connections")).toBeInTheDocument(); // the summary tiles are part of the normal page
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    const dlg = screen.getByRole("dialog", { name: "Graph, full screen" });
    expect(within(dlg).getByRole("img", { name: "Knowledge graph" }).getAttribute("class")).toContain("h-full"); // fills the height it is given
    expect(within(dlg).queryByText("Connections")).toBeNull(); // the tiles make room for the graph
    expect(within(dlg).queryByRole("button", { name: "Enlarge graph" })).toBeNull(); // already as large as it gets
    expect(document.body.style.overflow).toBe("hidden");
    expect(within(dlg).getByText("Select a node to see what it connects to.")).toBeVisible();
    await userEvent.click(within(dlg).getByRole("button", { name: "Hide details" }));
    expect(within(dlg).getByRole("button", { name: "Show details" })).toBeInTheDocument();
    expect(within(dlg).getByText("Select a node to see what it connects to.").closest("div[class*='hidden']")).not.toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Graph, full screen" })).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
    expect(screen.getByText("Connections")).toBeInTheDocument();
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

  describe("importing Word, PDF, CSV and zip files", () => {
    const repo = { id: "col1", name: "handbook", source: "docs", url: "", default_branch: "main", indexed_sha: null, status: "ready", file_count: 0, chunk_count: 0, updated_at: new Date().toISOString() };
    function setup(answer: unknown) {
      const calls: { url: string; body?: BodyInit | null }[] = [];
      vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
        const u = String(input);
        calls.push({ url: u, body: init?.body });
        if (u.endsWith("/auth/me")) return json(me);
        if (u.endsWith("/repositories")) return json([repo]);
        if (u.endsWith("/import") && init?.method === "POST") return json(answer);
        if (u.endsWith("/documents") && init?.method === "POST") return json({ received: 1, changed: 1 });
        if (u.endsWith("/documents")) return json([]);
        return json({}, 404);
      });
      return calls;
    }
    const sentFiles = (calls: { url: string; body?: BodyInit | null }[]) => (calls.find((c) => c.url.endsWith("/import"))!.body as FormData).getAll("files") as File[];

    it("sends a Word file as a real file upload for the server to read, and says what was imported and what was skipped", async () => {
      const calls = setup({ received: 2, changed: 1, secrets_redacted: 1, imported: [{ path: "Runbook.md", source: "Runbook.docx", kind: "docx" }], skipped: [{ name: "scan.pdf", reason: "it has no selectable text; it looks like a scanned document, and reading text out of pictures (OCR) is not supported" }] });
      renderPage("/knowledge?tab=docs");
      const input = await screen.findByLabelText("Upload documents");
      for (const ext of [".docx", ".pdf", ".zip", ".csv"]) expect(input).toHaveAttribute("accept", expect.stringContaining(ext));
      await userEvent.upload(input, [new File([new Uint8Array([80, 75, 3, 4])], "Runbook.docx"), new File([new Uint8Array([37, 80, 68, 70])], "scan.pdf")]);
      const result = await screen.findByLabelText("Import result");
      expect(result).toHaveTextContent("Imported 1 document, skipped 1, 1 secret masked.");
      expect(result).toHaveTextContent("scan.pdf");
      expect(result).toHaveTextContent("OCR");
      const files = sentFiles(calls);
      expect(files.map((f) => f.name)).toEqual(["Runbook.docx", "scan.pdf"]);
      expect(new Uint8Array(await files[0].arrayBuffer())).toEqual(new Uint8Array([80, 75, 3, 4])); // the real bytes, not an encoded copy
      expect(calls.some((c) => c.url.endsWith("/documents") && c.body)).toBe(false); // nothing went up as text
    });

    it("sends text files as text and Notion zips as files in the same pick", async () => {
      const calls = setup({ received: 1, changed: 3, secrets_redacted: 0, imported: [{ path: "a.md", source: "x.zip", kind: "notion" }, { path: "b.md", source: "x.zip", kind: "notion" }, { path: "c.md", source: "x.zip", kind: "notion" }], skipped: [] });
      renderPage("/knowledge?tab=docs");
      await userEvent.upload(await screen.findByLabelText("Upload documents"), [new File(["# Hi"], "hi.md"), new File([new Uint8Array([80, 75])], "Export.zip")]);
      expect(await screen.findByLabelText("Import result")).toHaveTextContent("Imported 3 documents.");
      expect(JSON.parse(calls.find((c) => c.url.endsWith("/documents") && c.body)!.body as string)).toEqual({ documents: [{ path: "hi.md", content: "# Hi" }] });
      expect(sentFiles(calls).map((f) => f.name)).toEqual(["Export.zip"]);
    });

    it("allows 25 MB a file, says so, and keeps a file over the limit, or over 100 MB in total, out of the upload", async () => {
      const calls = setup({ received: 1, changed: 1, secrets_redacted: 0, imported: [{ path: "ok.md", source: "ok.pdf", kind: "pdf" }], skipped: [] });
      renderPage("/knowledge?tab=docs");
      const hint = await screen.findByText(/Word, PDF, CSV and zip up to 25 MB each/);
      expect(hint).toHaveTextContent("Notion: Export as Markdown & CSV");
      expect(hint).toHaveTextContent("no OCR");
      const sized = (name: string, mb: number) => { const f = new File(["x"], name); Object.defineProperty(f, "size", { value: mb * 1024 * 1024 }); return f; };
      await userEvent.upload(screen.getByLabelText("Upload documents"), [sized("ok.pdf", 25), sized("toobig.pdf", 26)]);
      expect(await screen.findByText(/Skipped 1 file\(s\) over the size limit \(1 MB for text, 25 MB for Word/)).toBeInTheDocument();
      expect(sentFiles(calls).map((f) => f.name)).toEqual(["ok.pdf"]); // exactly 25 MB goes up; 26 MB does not
    });

    it("shows a warning for a very large document, next to what was imported", async () => {
      setup({ received: 1, changed: 1, secrets_redacted: 0, imported: [{ path: "Book.md", source: "Book.pdf", kind: "pdf" }], skipped: [],
        warnings: ["Book.pdf has 500 pages. Long documents add many sections that can crowd search results and the wiki's health counts. Documents your team works from, up to about 100 pages, work best. Only the first 400 pages were imported."] });
      renderPage("/knowledge?tab=docs");
      await userEvent.upload(await screen.findByLabelText("Upload documents"), new File([new Uint8Array([37, 80])], "Book.pdf"));
      const result = await screen.findByLabelText("Import result");
      expect(result).toHaveTextContent("Imported 1 document, 1 warning.");
      const warnings = within(result).getByRole("list", { name: "Warnings" });
      expect(warnings).toHaveTextContent("Book.pdf has 500 pages");
      expect(warnings).toHaveTextContent("up to about 100 pages, work best");
      expect(warnings).toHaveTextContent("Only the first 400 pages were imported");
    });

    it("shows no warning list for an ordinary import", async () => {
      setup({ received: 1, changed: 1, secrets_redacted: 0, imported: [{ path: "a.md", source: "a.docx", kind: "docx" }], skipped: [], warnings: [] });
      renderPage("/knowledge?tab=docs");
      await userEvent.upload(await screen.findByLabelText("Upload documents"), new File([new Uint8Array([80, 75])], "a.docx"));
      const result = await screen.findByLabelText("Import result");
      expect(result).toHaveTextContent("Imported 1 document.");
      expect(within(result).queryByRole("list", { name: "Warnings" })).toBeNull();
    });

    it("does not send more than 100 MB in one upload", async () => {
      const calls = setup({ received: 1, changed: 1, secrets_redacted: 0, imported: [], skipped: [] });
      renderPage("/knowledge?tab=docs");
      const sized = (name: string, mb: number) => { const f = new File(["x"], name); Object.defineProperty(f, "size", { value: mb * 1024 * 1024 }); return f; };
      await userEvent.upload(await screen.findByLabelText("Upload documents"), [sized("a.pdf", 25), sized("b.pdf", 25), sized("c.pdf", 25), sized("d.pdf", 25), sized("e.pdf", 25)]);
      expect(await screen.findByText(/1 file\(s\) did not fit in one upload \(100 MB at a time\)/)).toBeInTheDocument();
      expect(sentFiles(calls).map((f) => f.name)).toEqual(["a.pdf", "b.pdf", "c.pdf", "d.pdf"]);
    });
  });
});
