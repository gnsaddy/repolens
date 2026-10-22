import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Login, { SsoComplete } from "@/pages/Login";
import SettingsPage from "@/pages/SettingsPage";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
const wrap = (ui: React.ReactNode, url = "/") => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("SSO sign-in", () => {
  it("offers single sign-on only for e-mail addresses that have it, and shows provider errors", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.includes("/auth/sso/discover")) return json(u.includes("ana%40corp.io") ? { sso: true, organization: "Corp" } : { sso: false });
      return json({});
    });
    wrap(<Login />, "/login?sso_error=Your%20e-mail%20domain%20is%20not%20allowed");
    expect(screen.getByRole("alert")).toHaveTextContent("domain is not allowed");
    await userEvent.type(screen.getByLabelText("Email"), "bob@other.com");
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /single sign-on/ })).toBeNull();
    await userEvent.clear(screen.getByLabelText("Email"));
    await userEvent.type(screen.getByLabelText("Email"), "ana@corp.io");
    expect(await screen.findByRole("button", { name: "Continue with single sign-on (Corp)" })).toBeInTheDocument();
  });

  it("trades the one-time code for a session and never shows a token in the URL", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/sso/exchange") && init?.method === "POST") return json({ access_token: "jwt-abc", user: { id: "1", email: "ana@corp.io", name: "Ana", role: "viewer", org_id: "o" } });
      return json({});
    });
    wrap(<Routes><Route path="/sso-complete" element={<SsoComplete />} /><Route path="/" element={<div>home</div>} /></Routes>, "/sso-complete?code=" + "c".repeat(30));
    expect(await screen.findByText("home")).toBeInTheDocument();
    expect(localStorage.getItem("repolens.token")).toBe("jwt-abc");
    expect(JSON.parse(String(f.mock.calls.find((c) => String(c[0]).endsWith("/exchange"))![1]!.body))).toEqual({ code: "c".repeat(30) });
  });

  it("shows an error for a missing or rejected code", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ detail: "This sign-in expired. Start again." }, 401));
    wrap(<SsoComplete />, "/sso-complete?code=" + "d".repeat(30));
    expect(await screen.findByRole("alert")).toHaveTextContent("expired");
  });
});

describe("SSO settings", () => {
  it("admins configure SSO without ever seeing the secret and can mint a SCIM token once", async () => {
    const calls: { url: string; body?: unknown; method?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method && init.method !== "GET") calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/settings/sso") && init?.method === "PUT") return json({ configured: true, redirect_uri: "https://x/api/auth/sso/callback", issuer: "https://idp.example.com", client_id: "cid", has_secret: true, allowed_domains: ["corp.io"], default_role: "viewer", auto_provision: true, enabled: true });
      if (u.endsWith("/settings/sso")) return json({ configured: false, redirect_uri: "https://x/api/auth/sso/callback" });
      if (u.endsWith("/settings/scim/token")) return json({ token: "scim_secrettoken" });
      if (u.endsWith("/settings/scim")) return json({ configured: false, created_at: null, last_used_at: null });
      if (u.endsWith("/settings/llm")) return json({ provider: "heuristic", model: "claude-sonnet-5-5", base_url: "", has_key: false, source: "env", input_price: null, output_price: null, offline: true, presets: { anthropic: [], openai: [] }, embedding_provider: "hash" });
      if (u.endsWith("/settings/retrieval")) return json({ embedding: { provider: "hash", model: "x", dimensions: 1, offline: true }, rerank: { provider: "lexical", model: "m", configured: true, depth: 30 } });
      return json({});
    });
    localStorage.setItem("repolens.token", "tok");
    wrap(<SettingsPage />);
    await userEvent.type(await screen.findByLabelText("Issuer URL"), "https://idp.example.com");
    await userEvent.type(screen.getByLabelText("Client id"), "cid");
    await userEvent.type(screen.getByLabelText("Client secret"), "s3cret");
    await userEvent.type(screen.getByLabelText("Allowed e-mail domains"), "corp.io");
    await userEvent.click(within(screen.getByLabelText("Issuer URL").closest("form")!).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/settings/sso"))).toBe(true));
    expect(screen.queryByDisplayValue("s3cret")).toBeNull(); // the secret field is cleared after saving
    expect(await screen.findByText(/https:\/\/x\/api\/auth\/sso\/callback/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Generate token" }));
    expect(await screen.findByText("scim_secrettoken")).toBeInTheDocument();
  });
});
