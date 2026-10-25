import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import JiraPage from "@/pages/JiraPage";
import Sources from "@/pages/wiki/Sources";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const wrap = (ui: React.ReactNode, url = "/") => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);

describe("Jira filters", () => {
  it("builds JQL from project, status and text (every type unless one is chosen), and offers to import the result", async () => {
    const queries: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.endsWith("/jira/connection")) return json({ base_url: "https://acme.atlassian.net", deployment: "cloud", email: "", allow_publish: false, default_project: "" });
      if (u.endsWith("/jira/projects")) return json([{ key: "PAY", name: "Payments" }]);
      if (u.includes("/jira/issues")) { queries.push(decodeURIComponent(u.split("jql=")[1].split("&limit")[0])); return json([]); }
      return json({});
    });
    wrap(<JiraPage />);
    await screen.findByRole("option", { name: /Payments/ });
    await userEvent.selectOptions(screen.getByLabelText("Project"), "PAY");
    await userEvent.selectOptions(screen.getByLabelText("Status"), "done");
    await userEvent.type(screen.getByLabelText("Search text"), "rounding");
    await waitFor(() => expect(queries.at(-1)).toBe('project = "PAY" AND statusCategory = Done AND text ~ "rounding" ORDER BY updated DESC'));
    const link = screen.getByRole("link", { name: "Import these as searchable history" });
    expect(decodeURIComponent(link.getAttribute("href")!)).toContain('connector=jira&jql=project = "PAY"');
    await userEvent.click(screen.getByRole("button", { name: "Edit JQL" }));
    expect(screen.getByLabelText("JQL")).toHaveValue(queries.at(-1));
  });
});

describe("Confluence space browser", () => {
  it("lists spaces and pages and imports a page, a page with children, or the whole space", async () => {
    const posted: { url: string; mode: string; visibility: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/wiki/confluence/connection")) return json({ connected: true, base_url: "https://acme.atlassian.net/wiki", deployment: "cloud", email: "bot@acme.io" });
      if (u.endsWith("/wiki/confluence/spaces")) return json([{ key: "ENG", name: "Engineering", type: "global", import_url: "https://acme.atlassian.net/wiki/spaces/ENG" }]);
      if (u.endsWith("/spaces/ENG/pages")) return json({ truncated: false, pages: [
        { id: "100", title: "Handbook", parent_id: "", import_url: "https://x/pages/100" }, { id: "101", title: "Incident Response", parent_id: "100", import_url: "https://x/pages/101" }] });
      if (u.endsWith("/wiki/confluence/import") && init?.method === "POST") { posted.push(JSON.parse(String(init.body))); return json({ source_id: "s" }, 202); }
      if (u.endsWith("/wiki/connectors")) return json([]);
      return json([]);
    });
    wrap(<Sources overview={{ sources: [], totals: { sources: 0, pages: 0, sections: 0 }, health: {} as never, concepts: 0 }} />, "/wiki?tab=sources");
    await userEvent.click(await screen.findByRole("button", { name: /Browse spaces/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Engineering/ }));
    expect(await screen.findByText("Incident Response")).toBeInTheDocument(); // nested under Handbook
    await userEvent.click(screen.getByRole("button", { name: "Import page Incident Response" }));
    await userEvent.click(screen.getByRole("button", { name: "Import Handbook and its children" }));
    await userEvent.click(screen.getByRole("button", { name: "Import whole space" }));
    await waitFor(() => expect(posted).toHaveLength(3));
    expect(posted.map((p) => [p.url, p.mode])).toEqual([["https://x/pages/101", "page"], ["https://x/pages/100", "tree"], ["https://acme.atlassian.net/wiki/spaces/ENG", "space"]]);
    expect(posted.every((p) => p.visibility === "restricted")).toBe(true);
  });
});
