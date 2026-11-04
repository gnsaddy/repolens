import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import GithubLoginCard from "@/components/GithubLoginCard";
import { AuthProvider } from "@/lib/auth";
import type { GithubLoginSettings } from "@/lib/api";
import Login from "@/pages/Login";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

const wrap = (el: ReactElement) => <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter>{el}</MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>;

describe("sign in page", () => {
  function open(enabled: boolean) {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/github/enabled")) return json({ enabled });
      return json({}, 401);
    });
    render(wrap(<Login />));
  }

  it("offers GitHub only when the server and a workspace allow it", async () => {
    open(true);
    expect(await screen.findByRole("button", { name: "Continue with GitHub" })).toBeInTheDocument();
  });

  it("does not offer GitHub otherwise, and not when creating a workspace", async () => {
    open(false);
    await screen.findByRole("button", { name: "Sign in" });
    expect(screen.queryByRole("button", { name: "Continue with GitHub" })).toBeNull();
  });

  it("hides the button on the create-a-workspace form even when it is on", async () => {
    open(true);
    await screen.findByRole("button", { name: "Continue with GitHub" });
    await userEvent.click(screen.getByRole("button", { name: /Create a workspace/ }));
    expect(screen.queryByRole("button", { name: "Continue with GitHub" })).toBeNull();
  });
});

describe("admin card", () => {
  const base: GithubLoginSettings = { configured: false, app_configured: true, redirect_uri: "https://repolens.example/api/auth/github/callback" };

  function card(initial: GithubLoginSettings) {
    const calls: { method: string; body?: Record<string, unknown> }[] = [];
    let state = initial;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const method = init?.method ?? "GET";
      if (String(input).endsWith("/settings/github-login")) {
        if (method === "PUT") { const b = JSON.parse(String(init?.body)); calls.push({ method, body: b }); state = { ...state, configured: true, allowed_domains: String(b.allowed_domains).split(",").map((d: string) => d.trim()), default_role: b.default_role, auto_provision: b.auto_provision, enabled: b.enabled }; return json(state); }
        if (method === "DELETE") { calls.push({ method }); state = base; return json(null, 204); }
        return json(state);
      }
      return json({});
    });
    render(wrap(<GithubLoginCard />));
    return calls;
  }

  it("shows the callback URL, saves the allowed domains and the role, and can remove the setting", async () => {
    const calls = card(base);
    const region = await screen.findByLabelText("Sign in with GitHub");
    await within(region).findByLabelText("Allowed e-mail domains");  // the settings have loaded
    expect(region).toHaveTextContent("https://repolens.example/api/auth/github/callback");
    expect(within(region).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(within(region).getByLabelText("Allowed e-mail domains"), "acme.io, acme.com");
    await userEvent.selectOptions(within(region).getByLabelText("Role for new people"), "member");
    await userEvent.click(within(region).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls[0].body).toEqual({ allowed_domains: "acme.io, acme.com", default_role: "member", auto_provision: true, enabled: true }));
    expect(await within(region).findByText("on")).toBeInTheDocument();
    await userEvent.click(await within(region).findByRole("button", { name: "Remove" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("tells an admin the server has no OAuth app yet, and that the setting is still off", async () => {
    card({ ...base, app_configured: false });
    const region = await screen.findByLabelText("Sign in with GitHub");
    await within(region).findByLabelText("Allowed e-mail domains");
    expect(region).toHaveTextContent("no GitHub OAuth app yet");
    expect(region).toHaveTextContent("REPOLENS_GITHUB_LOGIN_CLIENT_ID");
    expect(within(region).getByText("off")).toBeInTheDocument();
  });
});
