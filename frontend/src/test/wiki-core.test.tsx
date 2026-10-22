import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Wiki from "@/pages/Wiki";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const health = { pages: 1, orphans: 0, broken_links: 0, stale: 0, duplicates: 0, undocumented_code: 0, outdated_references: 0, documented_code_ratio: 0.5, stale_days: 180 };
const overview = { sources: [
  { repo_id: "n1", name: "team-notes", kind: "collection", origin: "collection", pages: 1, visibility: "org", status: "ready", last_synced_at: null, web_url: "" },
  { repo_id: "c1", name: "payments", kind: "repo", origin: "repo", pages: 0, visibility: "org", status: "ready", last_synced_at: null, web_url: "" }], totals: { sources: 2, pages: 1, sections: 1 }, health, concepts: 0 };
const page = { repo_id: "n1", repo: "team-notes", path: "guide.md", title: "Guide", markdown: "# Guide\n\nHello.", unresolved_links: [], breadcrumbs: [], outline: [], deletable: true,
  source: { type: "upload", url: "", version: null, synced_at: null, updated_at: null, repo_url: "" }, backlinks: [], links_out: [], related: [], concepts: [], mentions_code: [] };

function setup(url = "/wiki") {
  const calls: { url: string; method?: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (init?.method && init.method !== "GET") calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (u.includes("/auth/me")) return json({ id: "u", email: "a@b.c", role: "admin", org_id: "o" });
    if (u.includes("/wiki/overview")) return json(overview);
    if (u.includes("/wiki/tree")) return json([{ key: "g", title: "Guide", path: "guide.md", children: [] }]);
    if (u.includes("/wiki/page/source")) return json({ content: "# Guide\n\nHello.", editable: true, reason: null, updated_at: "" });
    if (u.includes("/wiki/page") && init?.method === "PUT") return json({ path: JSON.parse(String(init.body)).path, created: false, secrets_redacted: 0 });
    if (u.includes("/wiki/page")) return json(page);
    if (u.includes("/wiki/search")) return json([{ repo: "team-notes", repo_id: "n1", path: "deploy.md", title: "Deploy checklist", snippet: "Run the migrations first", start_line: 1, score: 0.8 }]);
    if (u.includes("/wiki/atlas")) return json({ modules: [{ module: "src/billing", files: 3, functions: 4, classes: 1, languages: ["py"], documented: 1, coverage: 0.2, top_symbols: [{ node_id: "x1", name: "Invoice.charge", type: "function", path: "src/billing/invoice.py", start_line: 3, connections: 7, documented: false }] }], summary: { modules: 1, files: 3, public_symbols: 5, documented: 1, coverage: 0.2 } });
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={[url]}><Wiki /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

describe("DeepMind Wiki core", () => {
  it("searches every page by meaning and lists results with a snippet", async () => {
    setup();
    await userEvent.type(await screen.findByLabelText("Search pages"), "how to deploy");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    const list = await screen.findByRole("list", { name: "Search results" });
    expect(within(list).getByText("Deploy checklist")).toBeInTheDocument();
    expect(within(list).getByText(/migrations first/)).toBeInTheDocument();
  });

  it("edits a page in place and saves it", async () => {
    const calls = setup();
    await userEvent.click(await screen.findByRole("button", { name: /Edit/ }));
    const box = await screen.findByLabelText("Page content");
    await waitFor(() => expect(box).toHaveValue("# Guide\n\nHello."));
    await userEvent.type(box, " More.");
    await userEvent.click(screen.getByRole("button", { name: "Save page" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ repo_id: "n1", path: "guide.md", content: "# Guide\n\nHello. More." }));
  });

  it("creates a new page in a collection, adding .md to the path", async () => {
    const calls = setup();
    await userEvent.click(await screen.findByRole("button", { name: "New page" }));
    await userEvent.type(await screen.findByLabelText("Page path"), "runbooks/restart");
    await userEvent.click(screen.getByRole("button", { name: "Save page" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ repo_id: "n1", path: "runbooks/restart.md" }));
  });

  it("shows the code atlas with coverage and undocumented symbols", async () => {
    setup("/wiki?tab=atlas");
    expect(await screen.findByText("src/billing")).toBeInTheDocument();
    expect(screen.getByText("20% documented", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Invoice.charge")).toBeInTheDocument();
    expect(screen.getByText("undocumented")).toBeInTheDocument();
  });
});
