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

interface State { jira: unknown; confluence: unknown; github?: unknown; bitbucket?: unknown; sources?: unknown[] }
function setup(state: State, role = "admin", url = "/settings") {
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
    if (u.endsWith("/github/connection")) return json(state.github ?? { connected: false, api_base: "https://api.github.com" });
    if (u.endsWith("/bitbucket/connection")) return json(state.bitbucket ?? { connected: false, deployment: null, base_url: null, username: null });
    if (u.endsWith("/wiki/sources")) return json(state.sources ?? []);
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={[url]}><SettingsPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}
const none: State = { jira: null, confluence: { connected: false } };

describe("Settings: integrations", () => {
  it("lists every integration as available with a Connect button, and shows no single sign-on", async () => {
    setup(none);
    const available = await screen.findByRole("list", { name: "Available integrations" });
    for (const n of ["GitHub", "Bitbucket", "Jira", "Confluence", "Slack", "Notion", "Google Docs", "SharePoint"]) expect(within(available).getByText(n)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Connected" })).toBeInTheDocument();
    expect(screen.getByText(/Nothing connected yet/)).toBeInTheDocument();
    expect(screen.queryByText(/Single sign-on/i)).toBeNull();
    expect(screen.queryByText(/SCIM/i)).toBeNull();
  });

  it("Connect opens a card for the credentials; saving moves the integration to the Connected list and closes the card", async () => {
    const calls = setup(none);
    await userEvent.click(await screen.findByRole("button", { name: "Connect Jira" }));
    const dialog = screen.getByRole("dialog", { name: "Connect Jira" });
    await userEvent.type(within(dialog).getByLabelText("Jira URL"), "https://acme.atlassian.net");
    await userEvent.type(within(dialog).getByLabelText("Jira account email"), "bot@acme.io");
    await userEvent.type(within(dialog).getByLabelText("Jira token"), "tok-secret-123");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/jira/connection"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ base_url: "https://acme.atlassian.net", deployment: "cloud", email: "bot@acme.io", token: "tok-secret-123" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const connected = await screen.findByRole("list", { name: "Connected integrations" });
    expect(within(connected).getByText("Jira")).toBeInTheDocument();
    expect(within(connected).getByText(/acme.atlassian.net · bot@acme.io/)).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Available integrations" })).queryByText("Jira")).toBeNull();
    expect(screen.queryByDisplayValue("tok-secret-123")).toBeNull();
  });

  it("connected rows can be tested, configured and disconnected", async () => {
    const calls = setup({ jira: { base_url: "https://acme.atlassian.net", deployment: "cloud", email: "bot@acme.io", allow_publish: true, default_project: "" }, confluence: { connected: false } });
    const connected = await screen.findByRole("list", { name: "Connected integrations" });
    expect(within(connected).getByText(/can post comments/)).toBeInTheDocument();
    await userEvent.click(within(connected).getByRole("button", { name: "Test Jira" }));
    expect(await within(connected).findByText("Connected as Bot")).toBeInTheDocument();
    await userEvent.click(within(connected).getByRole("button", { name: "Configure Jira" }));
    expect(screen.getByLabelText("Jira URL")).toHaveValue("https://acme.atlassian.net"); // pre-filled, the token never is
    expect(screen.getByLabelText("Jira token")).toHaveValue("");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(within(connected).getByRole("button", { name: "Disconnect Jira" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/jira/connection"))).toBe(true));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Available integrations" })).getByText("Jira")).toBeInTheDocument());
  });

  it("Confluence, Bitbucket and GitHub each open their own form", async () => {
    setup(none);
    await userEvent.click(await screen.findByRole("button", { name: "Connect Confluence" }));
    expect(within(screen.getByRole("dialog")).getByLabelText("Confluence base URL")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: "Connect Bitbucket" }));
    const bb = within(screen.getByRole("dialog"));
    expect(bb.getByLabelText("Bitbucket token")).toHaveAttribute("type", "password");
    expect(bb.getByLabelText("Bitbucket username")).toBeInTheDocument(); // an Atlassian API token needs the email
    await userEvent.selectOptions(bb.getByLabelText("Bitbucket credential type"), "access");
    expect(bb.queryByLabelText("Bitbucket username")).toBeNull(); // access tokens have no username
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: "Connect GitHub" }));
    expect(within(screen.getByRole("dialog")).getByLabelText(/Personal access token/)).toBeInTheDocument();
  });

  it("wiki sources (Slack, Notion, Google Docs, SharePoint) link to the wiki and show a count once set up", async () => {
    setup({ ...none, sources: [{ id: "s1", kind: "slack", name: "Incidents chat" }, { id: "s2", kind: "slack", name: "Ops" }] });
    const available = await screen.findByRole("list", { name: "Available integrations" });
    expect(within(available).getByRole("link", { name: "Connect Notion" })).toHaveAttribute("href", "/wiki?tab=sources&connector=notion");
    const connected = await screen.findByRole("list", { name: "Connected integrations" });
    expect(within(connected).getByText("2 sources")).toBeInTheDocument();
    expect(within(connected).getByRole("link", { name: /Manage/ })).toHaveAttribute("href", "/wiki?tab=sources");
    expect(within(available).queryByText("Slack")).toBeNull();
  });

  it("non-admins see what is connected but get no buttons to change it", async () => {
    setup({ jira: { base_url: "https://acme.atlassian.net", deployment: "cloud", email: "x", allow_publish: false, default_project: "" }, confluence: { connected: false } }, "member");
    await screen.findByRole("list", { name: "Connected integrations" });
    expect(screen.queryByRole("button", { name: /Configure|Disconnect|Test/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Connect / })).toBeNull();
    expect(screen.getAllByText("Admins only").length).toBeGreaterThan(0);
  });

  it("keeps the language model and search quality on their own tab", async () => {
    setup(none, "admin", "/settings?tab=ai");
    expect(await screen.findByText("Language model")).toBeInTheDocument();
    expect(await screen.findByText("Search quality")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connected" })).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: /Integrations/ }));
    expect(await screen.findByRole("heading", { name: "Connected" })).toBeInTheDocument();
  });
});

describe("Bitbucket credential types", () => {
  it("sends the email with an Atlassian API token and nothing as a username for an access token", async () => {
    const calls = setup(none);
    await userEvent.click(await screen.findByRole("button", { name: "Connect Bitbucket" }));
    const d = within(screen.getByRole("dialog"));
    await userEvent.type(d.getByLabelText("Bitbucket username"), "me@example.com");
    await userEvent.type(d.getByLabelText("Bitbucket workspace"), "my-team");
    await userEvent.type(d.getByLabelText("Bitbucket token"), "ATATT3xFfGF0abc123");
    await userEvent.click(d.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/bitbucket/connection") && c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ deployment: "cloud", base_url: "", username: "me@example.com", token: "ATATT3xFfGF0abc123", workspaces: "my-team" });
  });
});
