import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import SettingsPage from "@/pages/SettingsPage";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const llm = { provider: "heuristic", model: "claude-sonnet-5-5", base_url: "", has_key: false, source: "env", input_price: null, output_price: null, offline: true, presets: { anthropic: [], openai: [] }, embedding_provider: "hash" };

function setup(state: { jira: unknown; confluence: unknown }, role = "admin") {
  const calls: { url: string; method?: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (init?.method && init.method !== "GET") calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role, org_id: "o" });
    if (u.endsWith("/settings/llm")) return json(llm);
    if (u.endsWith("/settings/retrieval")) return json({ embedding: { provider: "hash", model: "x", dimensions: 1, offline: true }, rerank: { provider: "lexical", model: "m", configured: true, depth: 30 } });
    if (u.endsWith("/jira/connection") && init?.method === "PUT") { state.jira = { base_url: "https://acme.atlassian.net", deployment: "cloud", email: "bot@acme.io", allow_publish: false, default_project: "" }; return json(state.jira); }
    if (u.endsWith("/jira/connection") && init?.method === "DELETE") { state.jira = null; return Promise.resolve(new Response(null, { status: 204 })); }
    if (u.endsWith("/jira/connection")) return json(state.jira);
    if (u.endsWith("/jira/test")) return json({ ok: true, user: "Bot" });
    if (u.endsWith("/wiki/confluence/connection") && init?.method === "PUT") { state.confluence = { connected: true, base_url: "https://acme.atlassian.net/wiki", deployment: "cloud", email: "bot@acme.io" }; return json(state.confluence); }
    if (u.endsWith("/wiki/confluence/connection")) return json(state.confluence);
    if (u.endsWith("/wiki/confluence/test")) return json({ ok: true, user: "RepoLens Bot" });
    if (u.endsWith("/github/connection")) return json({ connected: false, api_base: "https://api.github.com" });
    if (u.endsWith("/bitbucket/connection")) return json({ connected: false, deployment: null, base_url: null, username: null });
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><SettingsPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("Settings: integrations", () => {
  it("no longer shows single sign-on, and groups cards into AI model, code hosting and knowledge sources", async () => {
    setup({ jira: null, confluence: { connected: false } });
    await screen.findByText("Language model");
    expect(screen.queryByText(/Single sign-on/i)).toBeNull();
    expect(screen.queryByText(/SCIM/i)).toBeNull();
    for (const h of ["AI model", "Code hosting", "Knowledge sources"]) expect(screen.getByRole("heading", { name: h })).toBeInTheDocument();
    for (const t of ["GitHub", "Bitbucket", "Jira", "Confluence"]) expect(screen.getByRole("region", { name: t })).toBeInTheDocument();
  });

  it("shows a live connection summary", async () => {
    setup({ jira: { base_url: "https://acme.atlassian.net", deployment: "cloud", email: "x", allow_publish: false, default_project: "" }, confluence: { connected: false } });
    const strip = await screen.findByLabelText("Connection summary");
    await waitFor(() => expect(within(strip).getByText("Jira").closest("a")).toHaveTextContent("connected"));
    expect(within(strip).getByText("Confluence").closest("a")).toHaveTextContent("not connected");
    expect(within(strip).getByText("Jira").closest("a")).toHaveAttribute("href", "#knowledge");
  });

  it("connects Jira with the token kept in the request body only, then tests and disconnects", async () => {
    const calls = setup({ jira: null, confluence: { connected: false } });
    const jira = await screen.findByRole("region", { name: "Jira" });
    await userEvent.type(within(jira).getByLabelText("Jira URL"), "https://acme.atlassian.net");
    await userEvent.type(within(jira).getByLabelText("Jira account email"), "bot@acme.io");
    await userEvent.type(within(jira).getByLabelText("Jira token"), "tok-secret-123");
    await userEvent.click(within(jira).getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/jira/connection"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ base_url: "https://acme.atlassian.net", deployment: "cloud", email: "bot@acme.io", token: "tok-secret-123" });
    await userEvent.click(await within(jira).findByRole("button", { name: "Test connection" }));
    expect(await within(jira).findByText("Connected as Bot")).toBeInTheDocument();
    expect(within(jira).queryByDisplayValue("tok-secret-123")).toBeNull();
    await userEvent.click(within(jira).getByRole("button", { name: "Disconnect" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/jira/connection"))).toBe(true));
  });

  it("connects Confluence and offers to import pages", async () => {
    const calls = setup({ jira: null, confluence: { connected: false } });
    const conf = await screen.findByRole("region", { name: "Confluence" });
    await userEvent.type(within(conf).getByLabelText("Confluence base URL"), "https://acme.atlassian.net/wiki");
    await userEvent.type(within(conf).getByLabelText("Confluence account email"), "bot@acme.io");
    await userEvent.type(within(conf).getByLabelText("Confluence token"), "api-token-xyz");
    await userEvent.click(within(conf).getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/confluence/connection"))).toBe(true));
    expect(await within(conf).findByRole("link", { name: "Import pages →" })).toHaveAttribute("href", "/wiki?tab=sources");
    await userEvent.click(within(conf).getByRole("button", { name: "Test connection" }));
    expect(await within(conf).findByText("Authenticated as RepoLens Bot")).toBeInTheDocument();
  });

  it("non-admins see status but no credential fields", async () => {
    setup({ jira: null, confluence: { connected: false } }, "member");
    await screen.findByRole("region", { name: "Jira" });
    expect(screen.queryByLabelText("Jira token")).toBeNull();
    expect(screen.getAllByText(/An admin needs to connect/).length).toBe(2);
  });
});
