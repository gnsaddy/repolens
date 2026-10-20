import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AccessPanel from "@/components/AccessPanel";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
afterEach(() => vi.restoreAllMocks());

describe("AccessPanel", () => {
  it("restricts a repository to chosen non-admin users", async () => {
    let put: Record<string, unknown> | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/access") && init?.method === "PUT") { put = JSON.parse(init.body as string); return json({ visibility: "restricted", users: [{ id: "u2", email: "m1@acme.io", name: "" }] }); }
      if (u.endsWith("/access")) return json({ visibility: "org", users: [] });
      if (u.endsWith("/auth/users")) return json([{ id: "u1", email: "admin@acme.io", name: "", role: "admin", org_id: "o" }, { id: "u2", email: "m1@acme.io", name: "", role: "member", org_id: "o" }, { id: "u3", email: "v@acme.io", name: "", role: "viewer", org_id: "o" }]);
      return json({}, 404);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AccessPanel repoId="r1" repoName="secret-repo" /></QueryClientProvider>);
    await screen.findByText("Everyone in the organisation");
    expect(screen.queryByText("Users with access")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Restricted/ }));
    expect(await screen.findByText("Users with access")).toBeInTheDocument();
    expect(screen.queryByText("admin@acme.io")).not.toBeInTheDocument(); // admins always have access, so they are not listed
    await userEvent.click(screen.getByRole("checkbox", { name: /m1@acme.io/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save access" }));
    await waitFor(() => expect(put).toEqual({ visibility: "restricted", user_ids: ["u2"] }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
    expect(await screen.findByText("restricted")).toBeInTheDocument();
  });
});
