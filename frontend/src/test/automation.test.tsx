import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import AutomationPanel from "@/components/AutomationPanel";
import { FeedbackProvider } from "@/components/feedback";
import type { AutomationOverview } from "@/lib/api";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
afterEach(() => vi.restoreAllMocks());

const overview: AutomationOverview = {
  event: "ci_failure",
  all: { enabled: false, daily_limit: 5, all_branches: false },
  repositories: [{ id: "r1", name: "shop", enabled: true, daily_limit: 3, all_branches: false, own_rule: true }],
  used_today: 2,
  runs: [
    { id: "a1", repo: "shop", title: "CI failed on main", status: "started", reason: "Started 1 investigation(s)", link: "https://github.com/acme/shop/actions/runs/501", investigation_ids: ["i1"], created_at: new Date().toISOString() },
    { id: "a2", repo: "shop", title: "CI failed on dev", status: "skipped", reason: "Not the main branch (this rule watches only that branch)", link: "", investigation_ids: [], created_at: new Date().toISOString() },
  ],
};

function open() {
  const puts: { url: string; body: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (init?.method === "PUT") { puts.push({ url: u, body: JSON.parse(String(init.body)) }); return json({ enabled: true, daily_limit: 3, all_branches: false }); }
    if (u.endsWith("/automations")) return json(overview);
    return json({}, 404);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><MemoryRouter><AutomationPanel /></MemoryRouter></FeedbackProvider></QueryClientProvider>);
  return puts;
}

describe("automation panel", () => {
  it("explains what it does, that it is off until turned on, and that nothing is posted", async () => {
    open();
    await screen.findByLabelText("Investigate failures of shop");
    const card = screen.getByLabelText("Investigate CI failures");
    expect(card).toHaveTextContent("Nothing is posted or changed on GitHub");
    expect(card).toHaveTextContent("It runs as you");
    expect(card).toHaveTextContent("off until you turn it on");
    expect(card).toHaveTextContent("2 started in the last day");
    expect(within(card).getByLabelText("Investigate failures of Every repository")).not.toBeChecked();
    expect(within(card).getByLabelText("Investigate failures of shop")).toBeChecked();
  });

  it("saves a rule for the repository that was changed, with its limit and branch choice", async () => {
    const puts = open();
    const box = await screen.findByLabelText("Investigate failures of Every repository");
    await userEvent.click(box);
    const limit = screen.getByLabelText("Daily limit for Every repository");
    await userEvent.clear(limit);
    await userEvent.type(limit, "8");
    await userEvent.click(screen.getByLabelText("Every branch of Every repository"));
    const save = screen.getAllByRole("button", { name: "Save" })[0];
    await userEvent.click(save);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].url).toMatch(/\/automations\/rules\/all$/);
    expect(puts[0].body).toEqual({ enabled: true, daily_limit: 8, all_branches: true });
  });

  it("will not save a limit outside 1 to 50", async () => {
    const puts = open();
    const limit = await screen.findByLabelText("Daily limit for Every repository");
    await userEvent.clear(limit);
    await userEvent.type(limit, "0");
    expect(screen.getAllByRole("button", { name: "Save" })[0]).toBeDisabled();
    expect(puts).toHaveLength(0);
  });

  it("lists what ran, with the reason, the CI run and the investigations it started", async () => {
    open();
    await screen.findByText("CI failed on main");
    const runs = screen.getByLabelText("Recent automatic runs");
    expect(runs).toHaveTextContent("CI failed on main");
    expect(within(runs).getByRole("link", { name: /CI run/ })).toHaveAttribute("href", "https://github.com/acme/shop/actions/runs/501");
    expect(within(runs).getByRole("link", { name: "Investigation 1" })).toHaveAttribute("href", "/investigations/i1");
    expect(runs).toHaveTextContent("Not the main branch");
    expect(within(runs).getByText("skipped")).toBeInTheDocument();
  });
});
