import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Login, { SsoComplete } from "@/pages/Login";

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
