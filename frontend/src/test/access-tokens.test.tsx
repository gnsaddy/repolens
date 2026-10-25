import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Profile from "@/pages/Profile";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const tok = (o = {}) => ({ id: "t1", name: "Claude Code", prefix: "rlp_AbCd", scope: "read", created_at: new Date().toISOString(), expires_at: null, last_used_at: null, revoked: false, ...o });

function show(role = "member") {
  const calls: { url: string; method?: string; body?: unknown }[] = [];
  let tokens = [tok()];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (init?.method && init.method !== "GET") calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "Ana", role, org_id: "o", auth_source: "password" });
    if (u.endsWith("/auth/sessions")) return json([]);
    if (u.endsWith("/auth/api-tokens") && init?.method === "POST") { tokens = [...tokens, tok({ id: "t2", name: "CI", scope: "write" })]; return json({ ...tok({ id: "t2", name: "CI", scope: "write" }), token: "rlp_SECRET_VALUE_ONCE" }, 201); }
    if (u.endsWith("/auth/api-tokens")) return json(tokens);
    if (u.includes("/auth/api-tokens/") && init?.method === "DELETE") { tokens = tokens.map((t) => ({ ...t, revoked: true })); return json(null, 204); }
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><Profile /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("Access tokens", () => {
  it("creates a token, shows it once, and puts it into the editor setup snippets", async () => {
    const calls = show();
    await userEvent.type(await screen.findByLabelText("Token name"), "CI");
    await userEvent.selectOptions(screen.getByLabelText("Token access"), "write");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));
    expect(await screen.findByLabelText("New access token")).toHaveValue("rlp_SECRET_VALUE_ONCE");
    expect(calls.find((c) => c.url.endsWith("/auth/api-tokens"))?.body).toEqual({ name: "CI", scope: "write", days: 90 });
    await userEvent.click(screen.getByText(/Use RepoLens from your editor/));
    expect(screen.getByText(/claude mcp add --transport http repolens .*\/api\/mcp --header "Authorization: Bearer rlp_SECRET_VALUE_ONCE"/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByLabelText("New access token")).not.toBeInTheDocument(); // gone for good
  });
  it("lists tokens by prefix only, and revokes one after confirmation", async () => {
    const calls = show();
    const list = await screen.findByRole("list", { name: "Access tokens" });
    expect(within(list).getByText("Claude Code")).toBeInTheDocument();
    expect(within(list).getByText("rlp_AbCd…")).toBeInTheDocument();
    expect(within(list).getByText(/never used/)).toBeInTheDocument();
    await userEvent.click(within(list).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Revoke" }).length).toBe(2)); // the confirmation
    await userEvent.click(screen.getAllByRole("button", { name: "Revoke" }).at(-1)!);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/auth/api-tokens/t1"))).toBe(true));
  });
  it("offers viewers only read tokens", async () => {
    show("viewer");
    const select = await screen.findByLabelText("Token access");
    expect(within(select).queryByRole("option", { name: /start investigations/ })).not.toBeInTheDocument();
  });
});
