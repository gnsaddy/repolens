import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Login from "@/pages/Login";
import People from "@/pages/People";
import Profile from "@/pages/Profile";
import ResetPassword from "@/pages/ResetPassword";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());
const wrap = (el: React.ReactNode, url = "/") => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={[url]}>{el}</MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
const me = (extra = {}) => ({ id: "1", email: "ana@acme.io", name: "Ana", role: "admin", org_id: "o", auth_source: "password", totp_enabled: false, ...extra });

describe("Sign-in with an authenticator", () => {
  it("asks for the code after the password and sends it with the next attempt", async () => {
    const bodies: { code?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/sso/discover") || u.includes("/auth/sso/discover")) return json({ sso: false });
      if (u.endsWith("/auth/login")) {
        const b = JSON.parse(String(init?.body)); bodies.push(b);
        return b.code ? json({ access_token: "tok", token_type: "bearer", user: me() }) : json({ detail: "Authentication code required" }, 401);
      }
      return json({});
    });
    wrap(<Routes><Route path="/login" element={<Login />} /><Route path="/" element={<p>home</p>} /></Routes>, "/login");
    await userEvent.type(await screen.findByLabelText("Email"), "ana@acme.io");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const code = await screen.findByLabelText("Authentication code");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // asking for a code is not an error
    await userEvent.type(code, "123456");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(bodies.map((b) => b.code)).toEqual(["", "123456"]));
  });
});

describe("Invitation and reset links", () => {
  it("lets someone choose a password, checks both entries match, and confirms", async () => {
    const calls: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => { if (String(input).endsWith("/auth/reset")) { calls.push(JSON.parse(String(init?.body))); return json(null, 204); } return json({}); });
    wrap(<Routes><Route path="/reset" element={<ResetPassword />} /></Routes>, "/reset?token=abc123abc123abc123abc123");
    await userEvent.type(screen.getByLabelText("New password"), "Quartz-Lantern-2026");
    await userEvent.type(screen.getByLabelText("Repeat it"), "Quartz-Lantern-2027");
    expect(screen.getByRole("alert")).toHaveTextContent("do not match");
    expect(screen.getByRole("button", { name: "Set password" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Repeat it"));
    await userEvent.type(screen.getByLabelText("Repeat it"), "Quartz-Lantern-2026");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByText("Password set")).toBeInTheDocument();
    expect(calls).toEqual([{ token: "abc123abc123abc123abc123", password: "Quartz-Lantern-2026" }]);
  });
  it("explains a link with no token", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({}));
    wrap(<Routes><Route path="/reset" element={<ResetPassword />} /></Routes>, "/reset");
    expect(screen.getByRole("alert")).toHaveTextContent("incomplete");
  });
});

describe("People", () => {
  const people = [
    { id: "1", email: "ana@acme.io", name: "Ana", role: "admin", active: true, auth_source: "password", totp_enabled: true, pending: null, created_at: new Date().toISOString() },
    { id: "2", email: "bo@acme.io", name: "", role: "member", active: true, auth_source: "password", totp_enabled: false, pending: "invite", created_at: new Date().toISOString() }];
  it("invites someone and shows the one-time link once", async () => {
    localStorage.setItem("repolens.token", "tok");
    const posts: { url: string; body?: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method === "POST") posts.push({ url: u, body: init.body ? JSON.parse(String(init.body)) : undefined });
      if (u.endsWith("/auth/me")) return json(me());
      if (u.endsWith("/auth/people")) return json(people);
      if (u.endsWith("/auth/invites")) return json({ person: { ...people[1], email: "cy@acme.io" }, link: "/reset?token=ZZZ", expires_at: new Date(Date.now() + 864e5).toISOString() }, 201);
      return json({});
    });
    wrap(<People />);
    const t = await screen.findByRole("table", { name: "People" });
    expect(within(t).getByText("invited, not joined yet")).toBeInTheDocument();
    expect(within(t).getByText("authenticator")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Invite someone/ }));
    await userEvent.type(await screen.findByLabelText("E-mail"), "cy@acme.io");
    await userEvent.click(screen.getByRole("button", { name: "Create invitation" }));
    expect(await screen.findByLabelText("One-time link")).toHaveValue(`${window.location.origin}/reset?token=ZZZ`);
    expect(posts.find((p) => p.url.endsWith("/auth/invites"))?.body).toEqual({ email: "cy@acme.io", name: "", role: "member" });
  });
  it("is closed to members", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => (String(input).endsWith("/auth/me") ? json(me({ role: "member" })) : json({})));
    localStorage.setItem("repolens.token", "tok");
    wrap(<People />);
    expect(await screen.findByText(/Only workspace admins/)).toBeInTheDocument();
  });
});

describe("Profile: authenticator app", () => {
  it("walks through setup, shows the recovery codes once, then can be turned off", async () => {
    localStorage.setItem("repolens.token", "tok");
    const posts: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method === "POST") posts.push(u);
      if (u.endsWith("/auth/me")) return json(me());
      if (u.endsWith("/auth/sessions")) return json([]);
      if (u.endsWith("/auth/totp/setup")) return json({ secret: "JBSWY3DPEHPK3PXP", uri: "otpauth://totp/RepoLens%20AI:ana%40acme.io?secret=JBSWY3DPEHPK3PXP" });
      if (u.endsWith("/auth/totp/enable")) return json({ recovery_codes: ["aaaaaa-bbbbbb", "cccccc-dddddd"] });
      if (u.endsWith("/auth/totp/disable")) return json(null, 204);
      return json({});
    });
    wrap(<Profile />);
    await userEvent.click(await screen.findByRole("button", { name: "Set up" }));
    expect(await screen.findByLabelText("Setup key")).toHaveTextContent("JBSWY3DPEHPK3PXP");
    await userEvent.type(screen.getByLabelText("Six-digit code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Turn on" }));
    expect(await screen.findByLabelText("Recovery codes")).toHaveTextContent("aaaaaa-bbbbbb");
    await userEvent.click(screen.getByRole("button", { name: "I have saved them" }));
    expect(screen.queryByLabelText("Recovery codes")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Turn off…" }));
    await userEvent.type(screen.getByLabelText("Your password to confirm"), "correct-horse-1");
    await userEvent.type(screen.getByLabelText("Code to confirm"), "654321");
    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(posts.some((u) => u.endsWith("/auth/totp/disable"))).toBe(true));
    expect(await screen.findByRole("button", { name: "Set up" })).toBeInTheDocument();
  });
});

describe("People inside Settings", () => {
  const people = [{ id: "1", email: "ana@acme.io", name: "Ana", role: "admin", active: true, auth_source: "password", totp_enabled: false, pending: null, created_at: new Date().toISOString() }];
  const show = async (role: string, url: string) => {
    localStorage.setItem("repolens.token", "tok");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json(me({ role }));
      if (u.endsWith("/auth/people")) return json(people);
      return json({});
    });
    const { default: SettingsPage } = await import("@/pages/SettingsPage");
    wrap(<SettingsPage />, url);
  };
  it("is a tab of Settings for admins", async () => {
    await show("admin", "/settings?tab=people");
    expect(await screen.findByRole("tab", { name: /People/ })).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "People" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Invite someone/ })).toBeInTheDocument();
  });
  it("is not offered to members, even by address", async () => {
    await show("member", "/settings?tab=people");
    await screen.findByRole("tablist", { name: "Settings sections" });
    expect(screen.queryByRole("tab", { name: /People/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "People" })).not.toBeInTheDocument();
  });
});
