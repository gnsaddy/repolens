import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Sources from "@/pages/wiki/Sources";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const connectors = [
  { kind: "jira", label: "Jira tickets", description: "Search past incidents", needs_secret: false, fields: [{ key: "jql", label: "JQL query", required: true, secret: false }] },
  { kind: "slack", label: "Slack channels", description: "Threads", needs_secret: true, fields: [{ key: "channels", label: "Channels", required: true, secret: false }, { key: "token", label: "Bot token", type: "password", required: true, secret: true }] },
];

describe("Sources: connectors and schedule", () => {
  it("renders the connector form from the server schema, tests it, and imports with the secret kept separate", async () => {
    const calls: { url: string; body?: unknown; method?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method && init.method !== "GET") calls.push({ url: u, body: init.body ? JSON.parse(String(init.body)) : undefined, method: init.method });
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/wiki/connectors")) return json(connectors);
      if (u.endsWith("/check")) return json({ ok: true, detail: "Connected to Slack workspace Acme as bot" });
      if (u.endsWith("/import")) return json({ source_id: "s", repo_id: "r" }, 202);
      if (u.endsWith("/wiki/sources")) return json([{ id: "s1", repo_id: "r1", name: "Incidents chat", kind: "slack", label: "Slack · #incidents", url: "", mode: "sync", space_key: "", pages: 4, status: "completed", repo_status: "ready", error: null, last_synced_at: null, visibility: "restricted", sync_interval_hours: 6 }]);
      if (u.includes("/wiki/connectors/")) return json({});
      if (u.endsWith("/confluence/connection")) return json({ connected: false });
      return json({});
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter><Sources overview={{ sources: [], totals: { sources: 0, pages: 0, sections: 0 }, health: {} as never, concepts: 0 }} /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);

    expect(await screen.findByText(/Slack · #incidents/)).toBeInTheDocument();
    expect((screen.getByLabelText("Sync schedule for Incidents chat") as HTMLSelectElement).value).toBe("6");
    await userEvent.selectOptions(screen.getByLabelText("Sync schedule for Incidents chat"), "24");
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/schedule") && (c.body as { sync_interval_hours: number }).sync_interval_hours === 24)).toBe(true));

    await userEvent.click(await screen.findByRole("button", { name: /Slack channels/ }));
    expect(screen.getByLabelText("Bot token")).toHaveAttribute("type", "password");
    await userEvent.type(screen.getByLabelText("Channels"), "incidents");
    await userEvent.type(screen.getByLabelText("Bot token"), "xoxb-secret");
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Connected to Slack workspace Acme as bot")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("button", { name: "Test connection" }).closest("form")!).getByRole("button", { name: "Import" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/connectors/slack/import"))).toBe(true));
    const imp = calls.find((c) => c.url.endsWith("/connectors/slack/import"))!.body as { config: Record<string, string>; secret: string; visibility: string };
    expect(imp.secret).toBe("xoxb-secret");
    expect(imp.config).toEqual({ channels: "incidents" }); // the token never travels in config
    expect(imp.visibility).toBe("restricted");
  });
});
