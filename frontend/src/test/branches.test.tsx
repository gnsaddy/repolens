import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ForceGraph, { heatColor } from "@/components/ForceGraph";
import StoragePanel, { fmtBytes } from "@/components/StoragePanel";
import { AuthProvider } from "@/lib/auth";
import type { GraphEdge, GraphNode } from "@/lib/api";
import Repositories from "@/pages/Repositories";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter>{ui}</MemoryRouter></AuthProvider></QueryClientProvider>);
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

describe("branch picker", () => {
  it("lists branches, defaults to the default one, and posts every selected branch", async () => {
    const posted: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.includes("/repositories/branches")) return json({ branches: ["develop", "main"], default: "main" });
      if (u.endsWith("/repositories") && init?.method === "POST") { posted.push(JSON.parse(String(init.body))); return json({ id: "r1", name: "x" }, 201); }
      if (u.includes("/repositories/storage")) return json({ ttl_days: 30, total_bytes: 0, checkouts: [] });
      if (u.includes("/github/connection")) return json({ connected: false });
      return json([]);
    });
    wrap(<Repositories />);
    await userEvent.type(await screen.findByPlaceholderText("user-service"), "proj");
    await userEvent.type(screen.getByPlaceholderText("/srv/repos/user-service"), "/srv/proj");
    await userEvent.click(screen.getByRole("button", { name: "Choose branches" }));
    expect(await screen.findByLabelText(/^main/)).toBeChecked();
    await userEvent.click(screen.getByLabelText("develop"));
    await userEvent.click(screen.getByRole("button", { name: /Add & index/ }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ name: "proj", source: "local", url: "/srv/proj", branches: ["main", "develop"] });
  });
});

describe("StoragePanel", () => {
  it("shows clone sizes and reports what a cleanup freed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/repositories/storage")) return json({ ttl_days: 30, total_bytes: 2_500_000_000, checkouts: [{ path: "/d/1", repos: ["proj", "proj@dev"], exists: true, bytes: 2_500_000_000, last_used_at: null, indexing: false, partial: true }] });
      if (u.endsWith("/storage/cleanup")) return json({ removed: [{ path: "/d/1", repos: ["proj"], bytes: 2_500_000_000 }], freed_bytes: 2_500_000_000, dry_run: JSON.parse(String(init?.body)).dry_run });
      return json({});
    });
    wrap(<StoragePanel />);
    expect(await screen.findByText("proj, proj@dev")).toBeInTheDocument();
    expect(screen.getByText("blob-less")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Would free 2.5 GB from 1 clone(s)");
    expect(fmtBytes(1500)).toBe("2 KB");
  });
});

describe("graph heat and connections", () => {
  const node = (id: string, degree = 3): GraphNode => ({ id, type: "function", name: id, qualname: id, path: "a.py", start_line: 1, end_line: 2, repo_id: "r", repo: "r", degree, meta: {} });
  const edge: GraphEdge = { id: "e1", source: "a", target: "b", rel: "CALLS", provenance: "static_analysis", weight: 1, evidence: "" };

  it("maps heat from blue to red", () => {
    expect(heatColor(0)).toBe("rgb(59,130,246)");
    expect(heatColor(1)).toBe("rgb(239,68,68)");
    expect(heatColor(2)).toBe("rgb(239,68,68)");
  });
  it("colours nodes by heat, greys unknown ones, draws arrowheads and labels the focused links", async () => {
    const { container } = render(<ForceGraph nodes={[node("a"), node("b"), node("c")]} edges={[edge]} selectedId="a" heat={{ a: 1, b: 0 }} onSelect={() => {}} />);
    const drawing = container.querySelector("svg[aria-label='Knowledge graph']")!;
    const fills = [...drawing.querySelectorAll("g[role='button'] > circle")].map((c) => c.getAttribute("fill"));
    expect(fills).toEqual(["rgb(239,68,68)", "rgb(59,130,246)", "#e2e8f0"]);
    expect(container.querySelector("line")?.getAttribute("marker-end")).toBe("url(#arrow-CALLS)");
    expect(container.textContent).toContain("calls"); // edge label for the selected node's link
  });
  it("keeps type colours when there is no heat", () => {
    const { container } = render(<ForceGraph nodes={[node("a")]} edges={[]} selectedId={null} onSelect={() => {}} />);
    expect(container.querySelector("svg[aria-label='Knowledge graph'] g[role='button'] > circle")?.getAttribute("fill")).toBe("#4f46e5");
  });
});
