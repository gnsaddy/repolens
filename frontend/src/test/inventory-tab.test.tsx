import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import InventoryTab from "@/components/InventoryTab";
import type { Inventory, Repo } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

const repos = [{ id: "r1", name: "shop", source: "git" }, { id: "r2", name: "docs", source: "docs" }] as Repo[];
const full: Inventory = {
  repositories: { r1: "shop" },
  envvars: { count: 3, files: [{ repo_id: "r1", repo: "shop", file: "config.py", items: [{ name: "DATABASE_URL", detail: "read in the code", line: 2 }, { name: "API_KEY", detail: "setting, no default", line: 5 }] }, { repo_id: "r1", repo: "shop", file: ".env.example", items: [{ name: "PORT", detail: "Where the API listens", line: 1 }] }] },
  cli: { count: 1, files: [{ repo_id: "r1", repo: "shop", file: "Makefile", items: [{ name: "make check", detail: "lint and test", line: 3 }] }] },
  tables: { count: 0, files: [] },
};
const empty: Inventory = { repositories: {}, envvars: { count: 0, files: [] }, cli: { count: 0, files: [] }, tables: { count: 0, files: [] } };

function show(data: Inventory) {
  const urls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const u = String(input);
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "Ana", role: "member", org_id: "o" });
    if (u.includes("/inventory")) { urls.push(u); return json(data); }
    return json({}, 404);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><InventoryTab repos={repos} /></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return urls;
}

describe("inventory", () => {
  it("lists each kind with its count, grouped by file", async () => {
    show(full);
    const card = await screen.findByLabelText("Inventory");
    expect(await within(card).findByRole("tab", { name: "Environment variables 3" })).toHaveAttribute("aria-selected", "true");
    expect(within(card).getByRole("tab", { name: "Commands 1" })).toBeInTheDocument();
    expect(within(card).getByRole("tab", { name: "Database tables 0" })).toBeInTheDocument();
    const file = within(card).getByLabelText("shop config.py");
    expect(file).toHaveTextContent("DATABASE_URL");
    expect(file).toHaveTextContent("line 2");
    expect(within(card).getByLabelText("shop .env.example")).toHaveTextContent("Where the API listens");
    await userEvent.click(within(card).getByRole("tab", { name: "Commands 1" }));
    expect(within(card).getByLabelText("shop Makefile")).toHaveTextContent("make check");
    await userEvent.click(within(card).getByRole("tab", { name: "Database tables 0" }));
    expect(within(card).getByText("None found of this kind.")).toBeInTheDocument();
  });

  it("filters by name, file or detail and says how many are shown", async () => {
    show(full);
    const card = await screen.findByLabelText("Inventory");
    await within(card).findByLabelText("shop config.py");
    await userEvent.type(within(card).getByLabelText("Filter"), "api");
    expect(within(card).getByText("2 of 3 shown")).toBeInTheDocument(); // API_KEY, and PORT whose detail says "API"
  });

  it("asks for one repository when one is chosen, and offers only code repositories", async () => {
    const urls = show(full);
    const card = await screen.findByLabelText("Inventory");
    const pick = within(card).getByLabelText("Repository");
    expect(within(pick).queryByRole("option", { name: "docs" })).toBeNull();
    await userEvent.selectOptions(pick, "r1");
    await waitFor(() => expect(urls.at(-1)).toContain("/inventory?repo_id=r1"));
  });

  it("says how to fill it when nothing is listed yet", async () => {
    show(empty);
    expect(await screen.findByText(/click Re-index on a repository/)).toBeInTheDocument();
  });
});
