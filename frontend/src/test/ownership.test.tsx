import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import Ownership from "@/pages/Ownership";

const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const owner = (author: string, share: number) => ({ author, share, commits: 5, lines: 100, last_commit_at: new Date().toISOString() });

describe("Ownership", () => {
  it("shows areas with owners, flags knowledge risk, and looks up a path", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.endsWith("/repositories")) return json([{ id: "r1", name: "shop", source: "local", status: "ready" }, { id: "d1", name: "docs", source: "docs", status: "ready" }]);
      if (u.includes("/ownership/overview")) return json({ repo_id: "r1", repo: "shop", summary: { people: 2, areas: 2, bus_factor: 2, areas_at_risk: 1 },
        areas: [{ area: "billing", owners: [owner("bo", 0.7), owner("ana", 0.3)], bus_factor: 2, commits: 9, last_activity: new Date().toISOString(), risk: "ok" },
                { area: "auth", owners: [owner("ana", 1)], bus_factor: 1, commits: 3, last_activity: new Date().toISOString(), risk: "single_owner" }],
        people: [{ author: "ana", share: 0.6, commits: 8, last_commit_at: new Date().toISOString(), areas: ["auth", "billing"] }, { author: "bo", share: 0.4, commits: 6, last_commit_at: new Date().toISOString(), areas: ["billing"] }] });
      if (u.includes("/ownership/path")) return json({ path: "billing", owners: [owner("bo", 0.7)], bus_factor: 2 });
      return json([]);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter><Ownership /></MemoryRouter></AuthProvider></QueryClientProvider>);
    expect(await screen.findByText("One person knows this")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "bo 70%, ana 30%" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "docs" })).toBeNull(); // documentation collections have no commits
    await userEvent.type(screen.getByLabelText("Look up a file or folder"), "billing");
    expect(await screen.findByText("5 commits · last just now", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/Owners of/)).toBeInTheDocument();
  });
});
