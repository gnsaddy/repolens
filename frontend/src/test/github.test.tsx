import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import GitHubBrowse from "@/pages/GitHubBrowse";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const wrap = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><GitHubBrowse /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);

const repo = (name: string, extra = {}) => ({ full_name: `acme/${name}`, name, owner: "acme", description: `${name} service`, private: name === "orders", fork: false, archived: false, default_branch: "main", language: "Python", stars: 3, open_issues: 1, updated_at: new Date().toISOString(), html_url: `https://github.com/acme/${name}`, clone_url: `https://github.com/acme/${name}.git`, added_repo_id: null, ...extra });

describe("GitHub browser", () => {
  it("points to Settings when GitHub is not connected", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).endsWith("/auth/me") ? json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" }) : json({ connected: false, api_base: null }));
    wrap();
    expect(await screen.findByText(/GitHub is not connected/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings → GitHub" })).toHaveAttribute("href", "/settings");
  });

  it("lists repositories, shows pull requests and issues, and adds a repository with chosen branches", async () => {
    const posted: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.endsWith("/github/connection")) return json({ connected: true, api_base: "https://api.github.com", login: "ana", auth_method: "token" });
      if (u.includes("/github/repos?")) return json({ total: 2, page: 1, per_page: 25, truncated: false, items: [repo("orders"), repo("web", { added_repo_id: "r9" })] });
      if (u.includes("/pulls?state=open")) return json([{ number: 8, title: "Add retries", state: "open", draft: true, author: "bo", head: "retry", base: "main", labels: ["bug"], url: "https://github.com/acme/orders/pull/8", updated_at: null }]);
      if (u.includes("/issues?")) return json([{ number: 1, title: "Totals wrong", state: "open", comments: 4, author: "cy", labels: [], url: "https://github.com/acme/orders/issues/1", updated_at: null }]);
      if (u.endsWith("/branches")) return json([{ name: "main", protected: true }, { name: "develop", protected: false }]);
      if (u.endsWith("/repositories") && init?.method === "POST") { posted.push(JSON.parse(String(init.body))); return json({ id: "r1" }, 201); }
      return json([]);
    });
    wrap();
    const list = await screen.findByRole("list", { name: "Repositories" });
    expect(within(list).getByText("acme/orders")).toBeInTheDocument();
    expect(within(list).getByText("added")).toBeInTheDocument(); // web is already in RepoLens
    expect(screen.getByLabelText("private")).toBeInTheDocument();

    await userEvent.click(within(list).getByText("acme/orders"));
    const pr = await screen.findByRole("link", { name: "#8 Add retries" });
    expect(pr).toHaveAttribute("href", "https://github.com/acme/orders/pull/8");
    expect(pr).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByText("draft")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Issues/ }));
    expect(await screen.findByRole("link", { name: "#1 Totals wrong" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Add to RepoLens/ }));
    await userEvent.click(await screen.findByLabelText("develop"));
    await userEvent.click(screen.getByRole("button", { name: "Clone and index" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({ name: "orders", source: "git", url: "https://github.com/acme/orders.git", branches: ["main", "develop"] });
  });
});
