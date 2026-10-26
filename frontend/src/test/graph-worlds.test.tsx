import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ForceGraph, { layout, repoGroups } from "@/components/ForceGraph";
import { AuthProvider } from "@/lib/auth";
import type { GraphEdge, GraphNode } from "@/lib/api";
import Repositories from "@/pages/Repositories";

const node = (id: string, repo: string): GraphNode => ({ id, type: "function", name: id, qualname: id, path: `${id}.py`, start_line: 1, end_line: 2, repo_id: repo, repo, degree: 3, meta: {} });
const edge = (id: string, source: string, target: string): GraphEdge => ({ id, source, target, rel: "CALLS", provenance: "static_analysis", weight: 1, evidence: "" });
const ring = (repo: string, n: number) => Array.from({ length: n }, (_, i) => node(`${repo}${i}`, repo));
const chain = (repo: string, n: number) => Array.from({ length: n - 1 }, (_, i) => edge(`${repo}-e${i}`, `${repo}${i}`, `${repo}${i + 1}`));

const centre = (pos: ReturnType<typeof layout>, ids: string[]) => ({ x: ids.reduce((a, i) => a + pos[i].x, 0) / ids.length, y: ids.reduce((a, i) => a + pos[i].y, 0) / ids.length });
const spread = (pos: ReturnType<typeof layout>, ids: string[]) => { const c = centre(pos, ids); return Math.max(...ids.map((i) => Math.hypot(pos[i].x - c.x, pos[i].y - c.y))); };
const gap = (pos: ReturnType<typeof layout>, a: string[], b: string[]) => { const p = centre(pos, a), q = centre(pos, b); return Math.hypot(p.x - q.x, p.y - q.y); };

describe("graph worlds", () => {
  const a = ring("a", 8), b = ring("b", 8), c = ring("c", 8);
  const ids = (ns: GraphNode[]) => ns.map((n) => n.id);

  it("groups nodes by repository, biggest first", () => {
    expect(repoGroups([...a, ...ring("z", 12)]).map((g) => [g.id, g.ids.length])).toEqual([["z", 12], ["a", 8]]);
  });

  it("puts repositories that are not connected into separate worlds, apart from each other", () => {
    const pos = layout([...a, ...b], [...chain("a", 8), ...chain("b", 8)]);
    expect(gap(pos, ids(a), ids(b))).toBeGreaterThan(2 * Math.max(spread(pos, ids(a)), spread(pos, ids(b))));
  });

  it("draws worlds that are linked closer than worlds that are not, and a link reaches from one to the other", () => {
    const apart = layout([...a, ...b, ...c], [...chain("a", 8), ...chain("b", 8), ...chain("c", 8)]);
    const bridges = [edge("x1", "a0", "b0"), edge("x2", "a1", "b1"), edge("x3", "a2", "b2")];
    const linked = layout([...a, ...b, ...c], [...chain("a", 8), ...chain("b", 8), ...chain("c", 8), ...bridges]);
    expect(gap(linked, ids(a), ids(b))).toBeLessThan(0.9 * gap(apart, ids(a), ids(b))); // a and b moved towards each other
    expect(gap(linked, ids(a), ids(b))).toBeLessThan(gap(linked, ids(a), ids(c)) - 25); // and are nearer than the unconnected c
  });

  it("keeps one repository as a single cluster in the middle, as before", () => {
    const pos = layout(a, chain("a", 8));
    const c0 = centre(pos, ids(a));
    expect(Math.hypot(c0.x - 450, c0.y - 300)).toBeLessThan(120);
  });

  it("draws a labelled region per repository and marks links between them", () => {
    render(<ForceGraph nodes={[...a, ...b]} edges={[...chain("a", 8), edge("x1", "a0", "b0")]} selectedId={null} onSelect={() => {}} />);
    const svg = screen.getByRole("img", { name: "Knowledge graph" });
    expect(Array.from(svg.querySelectorAll("[data-world]")).map((g) => g.getAttribute("data-world")).sort()).toEqual(["a", "b"]);
    expect(svg).toHaveTextContent("a · 8 nodes");
    expect(svg.querySelectorAll("[data-bridge]")).toHaveLength(1);
  });

  it("shows no regions for a single repository", () => {
    render(<ForceGraph nodes={a} edges={chain("a", 8)} selectedId={null} onSelect={() => {}} />);
    const svg = screen.getByRole("img", { name: "Knowledge graph" });
    expect(svg.querySelectorAll("[data-world]")).toHaveLength(0);
    expect(svg.querySelectorAll("[data-bridge]")).toHaveLength(0);
  });
});

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
describe("a failed index", () => {
  beforeEach(() => localStorage.setItem("repolens.token", "tok"));
  afterEach(() => vi.restoreAllMocks());

  it("says why it failed and that a timeout is tried again, instead of blaming the path", async () => {
    const repo = { id: "r1", name: "tpm2-tss", source: "git", url: "https://github.com/tpm2-software/tpm2-tss", default_branch: "master", indexed_sha: null, status: "error", file_count: 0, chunk_count: 0, updated_at: new Date().toISOString(), last_error: "git log timed out" };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.endsWith("/repositories")) return json([repo]);
      if (u.includes("/repositories/storage")) return json({ ttl_days: 30, total_bytes: 0, checkouts: [] });
      return json([]);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter><Repositories /></MemoryRouter></AuthProvider></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("button", { name: /tpm2-tss/ }));  // pick it in the list, as a person does
    expect(await screen.findByText(/Indexing failed: git log timed out/)).toBeInTheDocument();
    expect(screen.getByText(/tried again automatically/)).toBeInTheDocument();
  });
});
