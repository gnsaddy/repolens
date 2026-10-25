import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import { APP_VERSION } from "@/lib/version";
import Login from "@/pages/Login";
import SettingsPage from "@/pages/SettingsPage";

afterEach(() => vi.restoreAllMocks());

describe("version", () => {
  it("is v0.1-beta and shown on the sign-in page", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(new Response(JSON.stringify({}), { status: 401, headers: { "Content-Type": "application/json" } })));
    expect(APP_VERSION).toBe("v0.1-beta");
    render(<QueryClientProvider client={new QueryClient()}><AuthProvider><MemoryRouter><Login /></MemoryRouter></AuthProvider></QueryClientProvider>);
    expect((await screen.findAllByText("v0.1-beta")).length).toBeGreaterThan(0);
  });

  it("the new logo is on the sign-in page and an About strip with the version is on Settings", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      const body = u.endsWith("/auth/me") ? { id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" } : {};
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    });
    localStorage.setItem("repolens.token", "tok");
    render(<QueryClientProvider client={new QueryClient()}><AuthProvider><MemoryRouter><SettingsPage /></MemoryRouter></AuthProvider></QueryClientProvider>);
    const about = await screen.findByRole("region", { name: "About RepoLens AI" });
    expect(about).toHaveTextContent("v0.1-beta");
    expect(about.querySelector("svg[aria-label='RepoLens AI logo']")).not.toBeNull();
    expect(screen.getByRole("link", { name: /Report a bug or request a feature/ })).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});
