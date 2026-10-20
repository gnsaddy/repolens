import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import SettingsPage from "@/pages/SettingsPage";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const base = { provider: "heuristic", model: "claude-sonnet-5-5", base_url: "", has_key: false, source: "env", input_price: null, output_price: null, offline: true, presets: { anthropic: ["claude-sonnet-5-5", "claude-haiku-5-5"], openai: [] }, embedding_provider: "hash" };

function setup(role: string, onPut?: (b: unknown) => void) {
  localStorage.setItem("repolens.token", "tok");
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role, org_id: "o" });
    if (u.endsWith("/settings/llm") && init?.method === "PUT") { const b = JSON.parse(init.body as string); onPut?.(b); return json({ ...base, provider: b.provider, model: b.model, has_key: true, source: "org", offline: false }); }
    if (u.endsWith("/settings/llm")) return json(base);
    return json({}, 404);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter><SettingsPage /></MemoryRouter></AuthProvider></QueryClientProvider>);
}
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("SettingsPage", () => {
  it("lets an admin pick a provider, paste a key and save; the key field is cleared afterwards", async () => {
    let sent: Record<string, unknown> | undefined;
    setup("admin", (b) => { sent = b as Record<string, unknown>; });
    await screen.findByText("offline mode");
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "anthropic");
    expect(screen.getByLabelText(/^Model/)).toHaveValue("claude-sonnet-5-5"); // preset filled in
    const key = screen.getByLabelText(/API key/);
    expect(key).toHaveAttribute("type", "password");
    await userEvent.type(key, "sk-ant-secret-123456");
    await userEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() => expect(sent).toMatchObject({ provider: "anthropic", model: "claude-sonnet-5-5", api_key: "sk-ant-secret-123456" }));
    expect(await screen.findByText("AI enabled")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText(/API key/)).toHaveValue(""));
    expect(screen.getByText(/stored — leave blank to keep/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("sk-ant-secret-123456");
  });

  it("is read-only for non-admins", async () => {
    setup("member");
    await screen.findByText("offline mode");
    expect(screen.getByLabelText("Provider")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.getByText(/Only workspace admins/)).toBeInTheDocument();
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
    return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter><SettingsPage /></MemoryRouter></AuthProvider></QueryClientProvider>);
  }

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
    await userEvent.click(await screen.findByRole("button", { name: "Connect with GitHub" }));
    expect(await screen.findByLabelText("Device code")).toHaveTextContent("ABCD-1234");
    expect(screen.getByRole("link", { name: /github.com\/login\/device/ })).toHaveAttribute("href", "https://github.com/login/device");
    expect(started).toEqual({ client_id: "Iv1.abc", scope: "repo read:user" });
    expect(await screen.findByText(/OAuth · @octo-admin/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Device code")).not.toBeInTheDocument();
    expect(polls).toBe(2);
  });

  it("explains a denied authorisation", async () => {
    mount("admin", (u, init) => {
      if (u.endsWith("/github/connection") && !init?.method) return json({ connected: false, api_base: null, oauth_client_id: "Iv1.abc" });
      if (u.endsWith("/device/start")) return json({ flow_id: "f1", user_code: "ZZZZ-0000", verification_uri: "https://github.com/login/device", expires_in: 900, interval: 0 });
      if (u.endsWith("/device/poll")) return json({ status: "denied" });
      return undefined;
    });
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
    await userEvent.click(await screen.findByText("Use a personal access token instead"));
    await userEvent.type(screen.getByLabelText(/Personal access token/), "github_pat_secret_value");
    await userEvent.click(screen.getByRole("button", { name: "Save token" }));
    await waitFor(() => expect(sent).toEqual({ token: "github_pat_secret_value", api_base: "https://api.github.com" }));
    await waitFor(() => expect(screen.getByLabelText(/Personal access token/)).toHaveValue(""));
  });

  it("disables the connect flow for non-admins", async () => {
    mount("member", (u) => (u.endsWith("/github/connection") ? json({ connected: true, api_base: "x", auth_method: "oauth", login: "o" }) : undefined));
    expect(await screen.findByText("GitHub is connected.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Connect with GitHub/ })).not.toBeInTheDocument();
  });
});
