import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import AutomationPanel from "@/components/AutomationPanel";
import { FeedbackProvider } from "@/components/feedback";
import type { AutomationOverview } from "@/lib/api";
import AgentPage from "@/pages/Agent";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
afterEach(() => vi.restoreAllMocks());

const off = { enabled: false, daily_limit: 5, all_branches: false };
const overview: AutomationOverview = {
  model: {
    ai: true, provider: "anthropic", model: "claude-sonnet-5-5", multi: true,
    routing: [
      { task: "Investigation reports", tier: "standard", provider: "anthropic", model: "claude-sonnet-5-5", why: "plain task, so a cheaper model is enough" },
      { task: "Pull request reviews", tier: "strong", provider: "anthropic", model: "claude-opus-5-5", why: "strong tier requested" },
    ],
  },
  events: {
    ci_failure: { all: off, repositories: [{ id: "r1", name: "shop", enabled: true, daily_limit: 3, all_branches: false, own_rule: true }], used_today: 2 },
    pr_review: { all: off, repositories: [{ id: "r1", name: "shop", ...off, own_rule: false }], used_today: 0 },
  },
  runs: [
    {
      id: "a1", event: "ci_failure", repo: "shop", title: "CI failed on main", status: "started", reason: "Started 1 investigation(s)", link: "https://github.com/acme/shop/actions/runs/501",
      created_at: new Date().toISOString(), files: [], reviews: [],
      investigations: [{ id: "i1", title: "CI failure: x", status: "completed", ai: true, models: ["claude-sonnet-5-5"], tokens: 1200 }],
    },
    {
      id: "a2", event: "ci_failure", repo: "shop", title: "CI failed on dev", status: "skipped", reason: "Not the main branch (this rule watches only that branch)", link: "",
      created_at: new Date().toISOString(), files: [], reviews: [], investigations: [],
    },
    {
      id: "a3", event: "pr_review", repo: "shop", title: "PR #7: Fix the user lookup", status: "started", reason: "2 review(s) of 1 file(s): 1 finding(s)", link: "https://github.com/acme/shop/pull/7",
      created_at: new Date().toISOString(), files: ["app/auth.py"], investigations: [],
      reviews: [
        { id: "p1", persona: "developer", status: "done", model: "claude-sonnet-5-5", findings: 1, summary: "", ai: true },
        { id: "p2", persona: "security-auditor", status: "failed", model: "", findings: 0, summary: "boom", ai: false },
      ],
    },
  ],
};

function open(data: AutomationOverview = overview, ui = <AutomationPanel />) {
  const puts: { url: string; body: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const u = String(input);
    if (init?.method === "PUT") { puts.push({ url: u, body: JSON.parse(String(init.body)) }); return json(off); }
    if (u.endsWith("/automations")) return json(data);
    return json({}, 404);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><MemoryRouter>{ui}</MemoryRouter></FeedbackProvider></QueryClientProvider>);
  return puts;
}

describe("which model wrote what", () => {
  it("marks a run as AI written with its model, or as evidence only", async () => {
    open();
    const runs = await screen.findByLabelText("Recent runs");
    await within(runs).findByText("CI failed on main");
    expect(runs).toHaveTextContent("AI written · claude-sonnet-5-5");
  });

  it("marks a run that no model wrote text for as evidence only", async () => {
    open({ ...overview, runs: [{ ...overview.runs[0], investigations: [{ ...overview.runs[0].investigations[0], ai: false, models: [] }] }] });
    const runs = await screen.findByLabelText("Recent runs");
    await within(runs).findByText("evidence only");
  });
});

describe("two switches, one for each thing the agent does", () => {
  it("shows both, off until turned on, each with its own count", async () => {
    open();
    const ci = await screen.findByLabelText("Investigate CI failures");
    const pr = screen.getByLabelText("Review new pull requests");
    expect(ci).toHaveTextContent("2 in the last day");
    expect(within(ci).getByLabelText("Investigate failures of shop")).toBeChecked();
    expect(within(pr).getByLabelText("Review pull requests of shop")).not.toBeChecked();
    expect(within(pr).getByLabelText("Review pull requests of Every repository")).not.toBeChecked();
    expect(pr).toHaveTextContent("Nothing is posted on the pull request");
  });

  it("saves a pull request rule with its event, limit and branch choice", async () => {
    const puts = open();
    const pr = await screen.findByLabelText("Review new pull requests");
    await userEvent.click(within(pr).getByLabelText("Review pull requests of Every repository"));
    const limit = within(pr).getByLabelText("Daily limit for Every repository (pr_review)");
    await userEvent.clear(limit);
    await userEvent.type(limit, "8");
    await userEvent.click(within(pr).getByLabelText("Every branch of Every repository (pr_review)"));
    await userEvent.click(within(pr).getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].url).toMatch(/\/automations\/rules\/all$/);
    expect(puts[0].body).toEqual({ event: "pr_review", enabled: true, daily_limit: 8, all_branches: true });
  });

  it("will not save a limit outside 1 to 50", async () => {
    const puts = open();
    const ci = await screen.findByLabelText("Investigate CI failures");
    const limit = within(ci).getByLabelText("Daily limit for Every repository (ci_failure)");
    await userEvent.clear(limit);
    await userEvent.type(limit, "0");
    expect(within(ci).getAllByRole("button", { name: "Save" })[0]).toBeDisabled();
    expect(puts).toHaveLength(0);
  });
});

describe("recent runs", () => {
  it("lists what ran, with the reason, the links, and the reviews with their findings and model", async () => {
    open();
    const runs = await screen.findByLabelText("Recent runs");
    await within(runs).findByText("CI failed on main");
    expect(within(runs).getByRole("link", { name: /CI run/ })).toHaveAttribute("href", "https://github.com/acme/shop/actions/runs/501");
    expect(within(runs).getByRole("link", { name: "Investigation 1" })).toHaveAttribute("href", "/investigations/i1");
    expect(runs).toHaveTextContent("Not the main branch");
    expect(within(runs).getByText("skipped")).toBeInTheDocument();
    expect(within(runs).getByRole("link", { name: /Pull request/ })).toHaveAttribute("href", "https://github.com/acme/shop/pull/7");
    expect(within(runs).getByRole("link", { name: "Developer review" })).toHaveAttribute("href", "/personas?run=p1");
    expect(runs).toHaveTextContent("1 finding");
    expect(runs).toHaveTextContent("Security auditor review");
    expect(runs).toHaveTextContent("Read: app/auth.py");
  });
});

describe("an older server", () => {
  it("says to restart the backend instead of crashing when the data has the old shape", async () => {
    open({ ...overview, events: undefined } as unknown as AutomationOverview);
    expect(await screen.findByRole("alert")).toHaveTextContent("The server is older than this page");
  });
});

describe("agent page", () => {
  it("explains both flows and what it never does, above the model and the switches", async () => {
    open(overview, <AgentPage />);
    const how = await screen.findByLabelText("How the agent works");
    for (const t of ["A CI run fails", "A pull request is opened"]) expect(how).toHaveTextContent(t);
    expect(how).toHaveTextContent("nothing is posted, commented or changed on GitHub");
    expect(how).toHaveTextContent("stops if they are no longer an admin");
    expect(screen.queryByLabelText("Which model runs this")).toBeNull();  // no card: each run says which model wrote it
    expect(await screen.findByLabelText("Investigate failures of shop")).toBeChecked();
  });
});
