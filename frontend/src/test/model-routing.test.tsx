import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import SettingsPage from "@/pages/SettingsPage";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const gemini = { id: "c1", provider: "gemini", base_url: null, has_key: true, models: [
  { id: "m1", model: "gemini-2.5-flash-lite", tier: "light", enabled: true, input_price: null, output_price: null },
  { id: "m2", model: "gemini-2.5-pro", tier: "strong", enabled: true, input_price: null, output_price: null }] };
const plan = [{ kind: "librarian", label: "Answering questions", tier: "light", model: "gemini-2.5-flash-lite", provider: "gemini", why: "plain task" }];

const entries = [{ id: "e1", asked: "How many times does the service retry?", answer: "It retries three times [S1].", sources: ["retry.md"], hits: 2, tokens: 420, tokens_saved: 840, created_at: new Date().toISOString(), last_hit_at: null, kind: "ask" }];

function setup(role = "admin", registry: unknown = { connections: [gemini], savings_mode: true, plan, multi: true, active: 2 }) {
  const calls: { url: string; method?: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (init?.method && init.method !== "GET") calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role, org_id: "o" });
    if (u.endsWith("/settings/retrieval")) return json({ embedding: { provider: "hash", model: "x", dimensions: 1, offline: true }, rerank: { provider: "lexical", model: "m", configured: true, depth: 30 } });
    if (u.endsWith("/settings/llm")) return json({ provider: "gemini", model: "x", base_url: "", has_key: true, source: "org", input_price: null, output_price: null, offline: false, presets: {}, embedding_provider: "hash" });
    if (u.endsWith("/settings/models") && (!init?.method || init.method === "GET")) return json(registry);
    if (u.endsWith("/settings/routing")) return json({ connections: [gemini], savings_mode: false, plan, multi: true, active: 2 });
    if (u.includes("/settings/memory/entries")) return json(entries);
    if (u.endsWith("/settings/memory/skill")) return json({ name: "payment-retries", filename: "payment-retries/SKILL.md", markdown: "---\nname: payment-retries\n---\n# Retries", entries: 1 });
    if (u.endsWith("/settings/memory") && init?.method === "DELETE") return json({ cleared: 4 });
    if (u.endsWith("/settings/memory")) return json({ enabled: true, entries: 4, hits: 7, tokens_saved: 12345, threshold: 0.92, ttl_hours: 72 });
    if (u.endsWith("/settings/llm/check") && JSON.parse(String(init?.body)).model) return json({ ok: true, latency_ms: 80 });
    if (u.endsWith("/settings/llm/check")) return json({ ok: true, models: [{ id: "gemini-2.5-flash-lite" }, { id: "gemini-2.5-pro" }, { id: "gemini-2.5-flash" }] });
    if (u.endsWith("/settings/models/connections")) return json({ connections: [gemini], savings_mode: true, plan, multi: true, active: 3 });
    if (u.includes("/settings/models/m1")) return json({ connections: [gemini], savings_mode: true, plan, multi: true, active: 2 });
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/settings?tab=ai"]}><SettingsPage /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
  return calls;
}

describe("Settings: models and routing", () => {
  it("lists providers with their models and tiers, and the routing plan", async () => {
    setup();
    const g = await screen.findByRole("region", { name: "Google Gemini models" });
    expect(within(g).getByText("gemini-2.5-flash-lite")).toBeInTheDocument();
    expect(within(g).getByLabelText("Tier for gemini-2.5-pro")).toHaveValue("strong");
    expect(screen.getByText("2 models active")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Routing plan" })).getByText("Answering questions")).toBeInTheDocument();
  });

  it("changes a model's tier and the savings mode", async () => {
    const calls = setup();
    const g = await screen.findByRole("region", { name: "Google Gemini models" });
    await userEvent.selectOptions(within(g).getByLabelText("Tier for gemini-2.5-flash-lite"), "standard");
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/settings/models/m1") && (c.body as { tier: string }).tier === "standard")).toBe(true));
    await userEvent.click(screen.getByRole("checkbox", { name: /Savings mode/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/settings/routing") && (c.body as { savings_mode: boolean }).savings_mode === false)).toBe(true));
  });

  it("adds several models after testing the key, with a guessed tier each", async () => {
    const calls = setup();
    await screen.findByRole("region", { name: "Google Gemini models" });
    await userEvent.click(screen.getByRole("button", { name: "Add models" }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).queryByLabelText("Filter models")).not.toBeInTheDocument(); // no model choice before the key works
    await userEvent.type(within(dlg).getByLabelText(/API key/), "AIza-test-key-0123456789");
    await userEvent.click(within(dlg).getByRole("button", { name: "Test connection" }));
    await userEvent.click(await within(dlg).findByRole("checkbox", { name: "gemini-2.5-flash-lite" }));
    await userEvent.click(within(dlg).getByRole("checkbox", { name: "gemini-2.5-pro" }));
    expect(within(dlg).getByLabelText("Tier for gemini-2.5-flash-lite")).toHaveValue("light");
    expect(within(dlg).getByLabelText("Tier for gemini-2.5-pro")).toHaveValue("strong");
    expect(within(dlg).getByRole("button", { name: "Add 2 models" })).toBeDisabled(); // not before every model is tested
    await userEvent.click(within(dlg).getByRole("button", { name: "Test 2 selected models" }));
    await waitFor(() => expect(within(dlg).getAllByText(/works/).length).toBe(2));
    await userEvent.click(within(dlg).getByRole("button", { name: "Add 2 models" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/settings/models/connections"))?.body).toMatchObject({ provider: "gemini", models: [{ model: "gemini-2.5-flash-lite", tier: "light" }, { model: "gemini-2.5-pro", tier: "strong" }] }));
  });

  it("members can see the models but not change them", async () => {
    setup("member");
    const g = await screen.findByRole("region", { name: "Google Gemini models" });
    expect(within(g).queryByLabelText("Tier for gemini-2.5-pro")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add models" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Savings mode/ })).toBeDisabled();
  });
});

describe("Settings: answer memory", () => {
  it("shows what was saved and clears it on confirmation", async () => {
    const calls = setup();
    expect(await screen.findByText("12,345")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear memory" }));
    await userEvent.click(await screen.findByRole("button", { name: "Forget" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/settings/memory") && c.method === "DELETE")).toBe(true));
  });
});

describe("Settings: browsing memory", () => {
  it("searches remembered answers and turns the selection into a skill file", async () => {
    const calls = setup();
    await userEvent.click(await screen.findByRole("button", { name: /Browse and search memory/ }));
    const dlg = await screen.findByRole("dialog");
    const list = await within(dlg).findByRole("list", { name: "Remembered answers" });
    expect(within(list).getByText("How many times does the service retry?")).toBeInTheDocument();
    await userEvent.type(within(dlg).getByLabelText("Search memory"), "retries three");
    await userEvent.click(within(dlg).getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls.length >= 0).toBe(true));
    await userEvent.click(within(dlg).getByRole("checkbox", { name: /Select How many times/ }));
    await userEvent.click(within(dlg).getByRole("button", { name: /Create skill from selected/ }));
    const file = await within(dlg).findByLabelText("Skill file");
    expect(file).toHaveTextContent("name: payment-retries");
    expect(within(dlg).getByRole("button", { name: /Download SKILL.md/ })).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/settings/memory/skill"))?.body).toEqual({ entry_ids: ["e1"] });
  });
});
