import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CommandPalette from "@/components/CommandPalette";
import { FeedbackProvider, useConfirm, useToast } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Wiki from "@/pages/Wiki";
import Dashboard from "@/pages/Dashboard";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const shell = (ui: React.ReactNode, url = "/") => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

function Probe() {
  const ask = useConfirm(), toast = useToast();
  return <><button onClick={async () => toast((await ask({ title: "Delete it?", confirmLabel: "Yes, delete", danger: true })) ? "deleted" : "kept")}>go</button></>;
}

describe("feedback", () => {
  it("confirms in an accessible dialog and reports the outcome in a toast", async () => {
    shell(<Probe />);
    await userEvent.click(screen.getByText("go"));
    expect(screen.getByRole("alertdialog", { name: "Delete it?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("kept")).toBeInTheDocument();
    await userEvent.click(screen.getByText("go"));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByText("deleted")).toBeInTheDocument();
  });
  it("closes the dialog with Escape as a no", async () => {
    shell(<Probe />);
    await userEvent.click(screen.getByText("go"));
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByText("kept")).toBeInTheDocument();
  });
});

describe("command palette", () => {
  function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div>; }
  it("filters pages, supports the keyboard and navigates", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.includes("/graph/nodes")) return json([{ id: "n1", type: "function", name: "compute_total", qualname: "shop.compute_total", repo: "shop", path: "a.py", degree: 1, meta: {}, repo_id: "r", start_line: 1, end_line: 2 }]);
      return json([]);
    });
    shell(<><CommandPalette open onClose={() => {}} /><Where /></>);
    const box = screen.getByLabelText(/Search pages/);
    await userEvent.type(box, "health");
    expect(screen.getByRole("option", { name: /DeepMind Wiki: health/ })).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("where")).toHaveTextContent("/wiki?tab=health");
  });
  it("finds code symbols through the graph search", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).includes("/graph/nodes") ? json([{ id: "n1", type: "function", name: "compute_total", qualname: "shop.compute_total", repo: "shop", path: "a.py", degree: 1, meta: {}, repo_id: "r", start_line: 1, end_line: 2 }]) : json([]));
    shell(<CommandPalette open onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText(/Search pages/), "compute");
    expect(await screen.findByText("shop.compute_total")).toBeInTheDocument();
  });
});

describe("wiki onboarding and delete", () => {
  const overview = (sources: unknown[]) => ({ sources, totals: { sources: sources.length, pages: sources.length ? 2 : 0, sections: 0 }, health: { pages: 0, orphans: 0, broken_links: 0, stale: 0, duplicates: 0, undocumented_code: 0, outdated_references: 0, documented_code_ratio: null, stale_days: 180 }, concepts: 0 });
  const src = { repo_id: "r1", name: "handbook", kind: "collection", origin: "collection", pages: 2, visibility: "org", status: "ready", last_synced_at: null, web_url: "" };
  const page = (extra: object) => ({ repo_id: "r1", repo: "handbook", path: "README.md", title: "Readme", markdown: "# Readme\nhello", unresolved_links: [], breadcrumbs: [], outline: [], source: { type: "collection", url: "", version: null, synced_at: null, updated_at: null, repo_url: "" }, backlinks: [], links_out: [], related: [], concepts: [], mentions_code: [], ...extra });

  it("shows a guided start when there is no documentation", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.includes("/wiki/overview")) return json(overview([]));
      return json([]);
    });
    shell(<Wiki />, "/wiki");
    expect(await screen.findByText("Start your wiki")).toBeInTheDocument();
    await userEvent.click(screen.getByText("A README or notes"));
    expect(await screen.findByText("Your collections")).toBeInTheDocument();
  });

  it("deletes an uploaded page after confirmation", async () => {
    let deleted = false;
    const f = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.includes("/wiki/overview")) return json(overview([src]));
      if (u.includes("/wiki/tree")) return json([{ key: "a", title: "Readme", path: "README.md", children: [] }]);
      if (u.includes("/wiki/page") && init?.method === "DELETE") { deleted = true; return Promise.resolve(new Response(null, { status: 204 })); }
      if (u.includes("/wiki/page")) return json(page({ deletable: true }));
      return json([]);
    });
    shell(<Wiki />, "/wiki");
    await userEvent.click(await screen.findByRole("button", { name: /Delete page/ }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent('Delete "Readme"?');
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete page" }));
    await waitFor(() => expect(deleted).toBe(true));
    expect(f).toHaveBeenCalled();
  });

  it("explains why synced and repository pages cannot be deleted", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.includes("/wiki/overview")) return json(overview([src]));
      if (u.includes("/wiki/tree")) return json([{ key: "a", title: "Readme", path: "README.md", children: [] }]);
      if (u.includes("/wiki/page")) return json(page({ deletable: false, managed_by: "confluence" }));
      return json([]);
    });
    shell(<Wiki />, "/wiki");
    expect(await screen.findByText("Synced from confluence")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete page/ })).toBeNull();
  });
});

describe("dashboard getting started", () => {
  it("lists the next steps and ticks what is done", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/dashboard")) return json({ repositories: 1, indexed_repositories: 1, investigations: 0, completed: 0, verified_accuracy: null, reviewed: 0, avg_duration_s: null, recent: [], top_repositories: [] });
      if (u.includes("/wiki/overview")) return json({ sources: [], totals: { sources: 0, pages: 0, sections: 0 }, health: {}, concepts: 0 });
      if (u.includes("/settings/llm")) return json({ provider: "heuristic" });
      return json({});
    });
    shell(<Routes><Route path="/" element={<Dashboard />} /></Routes>);
    const card = await screen.findByLabelText("Getting started");
    expect(card).toHaveTextContent("1 of 4 done");
    expect(card).toHaveTextContent("Add documentation");
  });
});
