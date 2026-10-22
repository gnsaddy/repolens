import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import SettingsPage from "@/pages/SettingsPage";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const base = { provider: "heuristic", model: "claude-sonnet-5-5", base_url: "", has_key: false, source: "env", input_price: null, output_price: null, offline: true, presets: { anthropic: ["claude-sonnet-5-5", "claude-haiku-5-5"], openai: [], gemini: ["gemini-2.5-flash", "gemini-2.5-pro"] }, embedding_provider: "hash" };

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

interface Flow { llm: Record<string, unknown>; calls: { url: string; method?: string; body?: Record<string, unknown> }[] }
function llmSetup(role = "admin", llm: Record<string, unknown> = base, checks: (body: Record<string, unknown>) => unknown = () => ({ ok: false, error: "unexpected" })): Flow {
  localStorage.setItem("repolens.token", "tok");
  const flow: Flow = { llm: { ...llm }, calls: [] };
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (init?.method && init.method !== "GET") flow.calls.push({ url: u, method: init.method, body });
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role, org_id: "o" });
    if (u.endsWith("/settings/llm") && init?.method === "PUT") { flow.llm = { ...base, ...body, has_key: true, source: "org", offline: false }; return json(flow.llm); }
    if (u.endsWith("/settings/llm") && init?.method === "DELETE") { flow.llm = { ...base }; return json(flow.llm); }
    if (u.endsWith("/settings/llm")) return json(flow.llm);
    if (u.endsWith("/settings/llm/check")) return json(checks(body));
    if (u.endsWith("/settings/llm/test")) return json({ ok: true, model: flow.llm.model, latency_ms: 812, input_tokens: 10 });
    if (u.endsWith("/settings/retrieval")) return json({ embedding: { provider: "hash", model: "x", dimensions: 1, offline: true }, rerank: { provider: "lexical", model: "m", configured: true, depth: 30 } });
    return json({}, 404);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/settings?tab=ai"]}><SettingsPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return flow;
}
const MODELS = [{ id: "gemini-2.5-flash", name: "Gemini 2.5 Flash" }, { id: "gemini-2.5-pro", name: "Gemini 2.5 Pro" }, { id: "gemini-3.5-flash-lite", name: "" }];
const geminiChecks = (b: Record<string, unknown>) => (b.model ? (b.model === "gemini-2.5-pro" ? { ok: false, error: "That model is not available to this key" } : { ok: true, model: b.model, latency_ms: 640, input_tokens: 9 }) : { ok: true, models: MODELS });

describe("Language model", () => {
  it("starts offline with a Connect button and no model selection", async () => {
    llmSetup();
    expect(await screen.findByText("Not connected: answers and reports use the built-in rules.")).toBeInTheDocument();
    expect(screen.getByText("Offline")).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Model/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Connect a language model" }));
    const d = within(screen.getByRole("dialog"));
    expect(d.getByLabelText("Provider")).toHaveValue("gemini");
    expect(d.queryByLabelText("Model name")).toBeNull(); // models only appear after the key is tested
    expect(d.queryByLabelText("Available models")).toBeNull();
    expect(d.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(d.getByRole("button", { name: "Test connection" })).toBeDisabled(); // no key yet
  });

  it("test key -> pick a model -> test it -> save; nothing is saved before the last step, and the key never comes back", async () => {
    const flow = llmSetup("admin", base, geminiChecks);
    await userEvent.click(await screen.findByRole("button", { name: "Connect a language model" }));
    const d = within(screen.getByRole("dialog"));
    await userEvent.type(d.getByLabelText(/API key/), "AIzaSecretKey0123456789");
    await userEvent.click(d.getByRole("button", { name: "Test connection" }));
    expect(await d.findByText("Key accepted. 3 models available.")).toBeInTheDocument();
    const list = within(await d.findByLabelText("Available models"));
    await userEvent.type(list.getByLabelText("Filter models"), "flash");
    expect(list.queryByRole("button", { name: "gemini-2.5-pro" })).toBeNull();
    // a model that fails its test keeps Save disabled and explains why
    await userEvent.clear(list.getByLabelText("Filter models"));
    await userEvent.click(list.getByRole("button", { name: "gemini-2.5-pro" }));
    await userEvent.click(d.getByRole("button", { name: "Test gemini-2.5-pro" }));
    expect(await d.findByText("That model is not available to this key")).toBeInTheDocument();
    expect(d.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.click(list.getByRole("button", { name: "gemini-3.5-flash-lite" }));
    await userEvent.click(d.getByRole("button", { name: "Test gemini-3.5-flash-lite" }));
    expect(await d.findByText(/Works — replied in 640 ms/)).toBeInTheDocument();
    expect(flow.calls.some((c) => c.method === "PUT")).toBe(false); // still nothing saved
    await userEvent.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(flow.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(flow.calls.find((c) => c.method === "PUT")!.body).toMatchObject({ provider: "gemini", model: "gemini-3.5-flash-lite", api_key: "AIzaSecretKey0123456789" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // the connected summary names the model in use
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("gemini-3.5-flash-lite")).toBeInTheDocument();
    expect(screen.getByText("Google Gemini")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("AIzaSecretKey0123456789");
  });

  it("a rejected key shows the reason and never lists models", async () => {
    llmSetup("admin", base, () => ({ ok: false, error: "Google rejected the key (400). Check it and its permissions." }));
    await userEvent.click(await screen.findByRole("button", { name: "Connect a language model" }));
    const d = within(screen.getByRole("dialog"));
    await userEvent.type(d.getByLabelText(/API key/), "bad-key-123456");
    await userEvent.click(d.getByRole("button", { name: "Test connection" }));
    expect(await d.findByRole("alert")).toHaveTextContent("rejected the key");
    expect(d.queryByLabelText("Available models")).toBeNull();
  });

  it("editing the key or provider after a test sends you back to step one", async () => {
    llmSetup("admin", base, geminiChecks);
    await userEvent.click(await screen.findByRole("button", { name: "Connect a language model" }));
    const d = within(screen.getByRole("dialog"));
    await userEvent.type(d.getByLabelText(/API key/), "AIzaSecretKey0123456789");
    await userEvent.click(d.getByRole("button", { name: "Test connection" }));
    await d.findByLabelText("Available models");
    await userEvent.type(d.getByLabelText(/API key/), "x");
    expect(d.queryByLabelText("Available models")).toBeNull();
  });

  it("a service that cannot list models lets you type the model name", async () => {
    llmSetup("admin", base, (b) => (b.model ? { ok: true, model: b.model, latency_ms: 300, input_tokens: 5 } : { ok: true, models: [] }));
    await userEvent.click(await screen.findByRole("button", { name: "Connect a language model" }));
    const d = within(screen.getByRole("dialog"));
    await userEvent.selectOptions(d.getByLabelText("Provider"), "openai");
    await userEvent.type(d.getByLabelText("Base URL"), "https://llm.example.com/v1");
    await userEvent.type(d.getByLabelText(/API key/), "sk-test-0123456789");
    await userEvent.click(d.getByRole("button", { name: "Test connection" }));
    await userEvent.type(await d.findByLabelText("Model name"), "my-model");
    await userEvent.click(d.getByRole("button", { name: "Test my-model" }));
    expect(await d.findByText(/Works/)).toBeInTheDocument();
    expect(d.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("when connected: shows provider, model in use and key source; Test, Change model and Disconnect work", async () => {
    const flow = llmSetup("admin", { ...base, provider: "gemini", model: "gemini-2.5-flash", has_key: true, source: "org", offline: false }, geminiChecks);
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("gemini-2.5-flash")).toBeInTheDocument();
    expect(screen.getByText("workspace setting")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Replied in 812 ms (10 input tokens)")).toBeInTheDocument();
    // changing the model re-uses the stored key: no need to paste it again
    await userEvent.click(screen.getByRole("button", { name: "Change model" }));
    const d = within(screen.getByRole("dialog"));
    expect(d.getByText(/stored — leave blank to keep/)).toBeInTheDocument();
    await userEvent.click(d.getByRole("button", { name: "Test connection" }));
    await userEvent.click(await d.findByRole("button", { name: "gemini-2.5-flash" }));
    await userEvent.click(d.getByRole("button", { name: "Test gemini-2.5-flash" }));
    await d.findByText(/Works/);
    await userEvent.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(flow.calls.some((c) => c.method === "PUT" && c.body?.api_key === "")).toBe(true)); // a blank key means "keep the stored one"
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Offline")).toBeInTheDocument();
  });

  it("is read-only for non-admins", async () => {
    llmSetup("member");
    expect(await screen.findByText(/Only workspace admins can connect a language model/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Connect a language model|Change model|Disconnect/ })).toBeNull();
  });
});

describe("GitHub connect", () => {
  function mount(role: string, routes: (u: string, init?: RequestInit) => Promise<Response> | undefined) {
    localStorage.setItem("repolens.token", "tok");
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role, org_id: "o" });
      if (u.endsWith("/settings/llm")) return json(base);
      return routes(u, init) ?? json({}, 404);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={qc}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/settings"]}><SettingsPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  }
  const openGithub = async () => { await userEvent.click(await screen.findByRole("button", { name: "Connect GitHub" })); };
  const deviceFlow = async () => { await userEvent.click(await screen.findByRole("button", { name: /Or sign in with GitHub/ })); };

  it("runs the device flow: shows the code, polls, then shows the connected account", async () => {
    let connected = false;
    let polls = 0;
    let started: Record<string, string> | undefined;
    mount("admin", (u, init) => {
      if (u.endsWith("/github/connection") && !init?.method) return json(connected ? { connected: true, api_base: "https://api.github.com", auth_method: "oauth", login: "octo-admin", oauth_client_id: "Iv1.abc" } : { connected: false, api_base: null, oauth_client_id: "Iv1.abc" });
      if (u.endsWith("/github/oauth/device/start")) { started = JSON.parse(init!.body as string); return json({ flow_id: "f1", user_code: "ABCD-1234", verification_uri: "https://github.com/login/device", expires_in: 900, interval: 0 }); }
      if (u.endsWith("/github/oauth/device/poll")) { polls++; if (polls < 2) return json({ status: "pending", interval: 0 }); connected = true; return json({ status: "success" }); }
      return undefined;
    });
    await openGithub();
    await deviceFlow();
    await userEvent.click(await screen.findByRole("button", { name: "Connect with GitHub" }));
    expect(await screen.findByLabelText("Device code")).toHaveTextContent("ABCD-1234");
    expect(screen.getByRole("link", { name: /github.com\/login\/device/ })).toHaveAttribute("href", "https://github.com/login/device");
    expect(started).toEqual({ client_id: "Iv1.abc", scope: "repo read:user" });
    expect(await screen.findByText(/OAuth · @octo-admin/)).toBeInTheDocument(); // moved to the Connected list
    expect(screen.queryByLabelText("Device code")).not.toBeInTheDocument(); // the card closed itself
    expect(polls).toBe(2);
  });

  it("explains a denied authorisation", async () => {
    mount("admin", (u, init) => {
      if (u.endsWith("/github/connection") && !init?.method) return json({ connected: false, api_base: null, oauth_client_id: "Iv1.abc" });
      if (u.endsWith("/device/start")) return json({ flow_id: "f1", user_code: "ZZZZ-0000", verification_uri: "https://github.com/login/device", expires_in: 900, interval: 0 });
      if (u.endsWith("/device/poll")) return json({ status: "denied" });
      return undefined;
    });
    await openGithub();
    await deviceFlow();
    await userEvent.click(await screen.findByRole("button", { name: "Connect with GitHub" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("denied");
  });

  it("still supports a personal access token without echoing it back", async () => {
    let sent: Record<string, string> | undefined;
    mount("admin", (u, init) => {
      if (u.endsWith("/github/connection") && init?.method === "PUT") { sent = JSON.parse(init.body as string); return json({ connected: true, api_base: "https://api.github.com", auth_method: "token" }); }
      if (u.endsWith("/github/connection")) return json({ connected: false, api_base: null });
      return undefined;
    });
    await openGithub();
    await userEvent.type(screen.getByLabelText(/Personal access token/), "github_pat_secret_value");
    await userEvent.click(screen.getByRole("button", { name: "Save token" }));
    await waitFor(() => expect(sent).toEqual({ token: "github_pat_secret_value", api_base: "https://api.github.com" }));
    await waitFor(() => expect(screen.queryByLabelText(/Personal access token/)).toBeNull()); // the card closed; the token is not kept in the page
  });

  it("disables the connect flow for non-admins", async () => {
    mount("member", (u) => (u.endsWith("/github/connection") ? json({ connected: true, api_base: "x", auth_method: "oauth", login: "o" }) : undefined));
    expect(await screen.findByText(/OAuth · @o/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Configure GitHub|Disconnect GitHub|Connect GitHub/ })).not.toBeInTheDocument();
  });
});

