import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import Wiki from "@/pages/Wiki";
import Markdown from "@/pages/wiki/Markdown";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const health = { pages: 2, orphans: 1, broken_links: 1, stale: 0, duplicates: 0, undocumented_code: 0, outdated_references: 0, documented_code_ratio: 0.5, stale_days: 180 };
const overview = { sources: [{ repo_id: "r1", name: "handbook", kind: "collection", origin: "confluence", pages: 2, visibility: "restricted", status: "ready", last_synced_at: null, web_url: "" }], totals: { sources: 1, pages: 2, sections: 4 }, health, concepts: 3 };
const tree = [{ key: "a", title: "Engineering", path: null, children: [{ key: "b", title: "Incident Response", path: "eng/incident.md", children: [] }] }];
const page = {
  repo_id: "r1", repo: "handbook", path: "eng/incident.md", title: "Incident Response", markdown: "# Incident Response\n\nSee [Runbook](wiki://r1/eng/runbook.md) and [bad](javascript:alert(1)).",
  unresolved_links: [{ link: "wiki:Missing" }], breadcrumbs: [{ title: "Engineering", path: null }, { title: "Incident Response", path: "eng/incident.md" }], outline: [],
  source: { type: "confluence", url: "https://x.atlassian.net/wiki/spaces/ENG/pages/1", version: 3, synced_at: null, updated_at: null, repo_url: "" },
  backlinks: [{ repo: "handbook", repo_id: "r1", path: "eng/oncall.md", title: "On-call Guide" }], links_out: [], related: [], concepts: ["pager"], mentions_code: [],
};

function mock() {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const u = String(input);
    if (u.includes("/auth/me")) return json({ id: "u", email: "a@b.c", role: "admin", org_id: "o" });
    if (u.includes("/wiki/overview")) return json(overview);
    if (u.includes("/wiki/tree")) return json(tree);
    if (u.includes("/wiki/page")) return json(page);
    if (u.includes("/wiki/sources")) return json([]);
    if (u.includes("/wiki/confluence/connection")) return json({ connected: false });
    return json({});
  });
}
const renderWiki = (url = "/wiki") => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter initialEntries={[url]}><Wiki /></MemoryRouter></AuthProvider></QueryClientProvider>);

beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

describe("Wiki", () => {
  it("shows stats and the first page with backlinks, source badge and a broken-link warning", async () => {
    mock();
    renderWiki();
    expect(await screen.findByRole("heading", { name: "Incident Response", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("confluence")).toBeInTheDocument();
    expect(screen.getByText("On-call Guide")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Missing");
    expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(await screen.findByText("handbook (2)")).toBeInTheDocument();
  });
  it("offers Sources when empty and shows Confluence form for admins", async () => {
    mock();
    renderWiki("/wiki?tab=sources");
    expect(await screen.findByText("Add README / text files")).toBeInTheDocument();
    expect(await screen.findByLabelText("Base URL")).toBeInTheDocument();
    expect(screen.getByLabelText("Token")).toHaveAttribute("type", "password");
  });
  it("switches tabs", async () => {
    mock();
    renderWiki();
    await userEvent.click(await screen.findByRole("tab", { name: /Ask/ }));
    expect(screen.getByLabelText("Question")).toBeInTheDocument();
  });
});

describe("Markdown", () => {
  it("routes wiki:// links in-app and drops javascript: links", async () => {
    const open = vi.fn();
    render(<Markdown text={"[Runbook](wiki://r1/eng/runbook.md#steps) [bad](javascript:alert(1)) [ext](https://example.com)"} onOpen={open} />);
    await userEvent.click(screen.getByText("Runbook"));
    expect(open).toHaveBeenCalledWith("r1", "eng/runbook.md", "steps");
    expect(screen.getByText("bad").closest("a")?.getAttribute("href") ?? "").not.toContain("javascript");
    expect(screen.getByText("ext").closest("a")).toHaveAttribute("target", "_blank");
  });
});
