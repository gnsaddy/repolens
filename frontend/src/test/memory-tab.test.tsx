import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Knowledge from "@/pages/Knowledge";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());
const entries = [{ id: "e1", asked: "How many times does the service retry?", answer: "It retries three times [S1].", sources: ["retry.md"], hits: 2, tokens: 420, tokens_saved: 840, created_at: new Date().toISOString(), last_hit_at: null, kind: "ask" }];

describe("Knowledge → Memory tab", () => {
  it("shows savings, searches the memory and packages a selection as a skill", async () => {
    const calls: { url: string; body?: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method === "POST") calls.push({ url: u, body: init.body ? JSON.parse(String(init.body)) : undefined });
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "admin", org_id: "o" });
      if (u.endsWith("/repositories")) return json([]);
      if (u.endsWith("/settings/memory")) return json({ enabled: true, entries: 1, hits: 2, tokens_saved: 840, threshold: 0.92, ttl_hours: 72 });
      if (u.includes("/settings/memory/entries")) return json(entries);
      if (u.endsWith("/settings/memory/skill")) return json({ name: "retries", filename: "retries/SKILL.md", markdown: "---\nname: retries\n---\n# Retries", entries: 1 });
      return json({});
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/knowledge?tab=memory"]}><Knowledge /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    expect(await screen.findByText("840")).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Remembered answers" });
    expect(within(list).getByText("How many times does the service retry?")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search memory"), "retries three");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Select How many times/ }));
    await userEvent.click(screen.getByRole("button", { name: /Create skill from selected/ }));
    expect(await screen.findByLabelText("Skill file")).toHaveTextContent("name: retries");
    expect(screen.getByRole("button", { name: /Download SKILL.md/ })).toBeInTheDocument();
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/settings/memory/skill"))?.body).toEqual({ entry_ids: ["e1"] }));
  });
});
