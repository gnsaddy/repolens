import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import { APP_VERSION } from "@/lib/version";
import Login from "@/pages/Login";

afterEach(() => vi.restoreAllMocks());

describe("version", () => {
  it("is v0.1-beta and shown on the sign-in page", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(new Response(JSON.stringify({}), { status: 401, headers: { "Content-Type": "application/json" } })));
    expect(APP_VERSION).toBe("v0.1-beta");
    render(<QueryClientProvider client={new QueryClient()}><AuthProvider><MemoryRouter><Login /></MemoryRouter></AuthProvider></QueryClientProvider>);
    expect((await screen.findAllByText("v0.1-beta")).length).toBeGreaterThan(0);
  });
});
