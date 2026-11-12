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
    drift_draft: { all: off, repositories: [{ id: "r1", name: "shop", ...off, own_rule: false }], used_today: 0 },
    watch: { all: off, repositories: [{ id: "r1", name: "shop", ...off, own_rule: false }], used_today: 0 },
  },
  controls: { paused: false, token_budget: 0, tokens_used: 4200, failed_24h: 1, stuck: 0, problems: ["1 automatic run failed in the last day"] },
  runs: [
    {
      id: "a1", event: "ci_failure", repo: "shop", title: "CI failed on main", status: "started", reason: "Started 1 investigation(s)", link: "https://github.com/acme/shop/actions/runs/501",
      created_at: new Date().toISOString(), files: [], reviews: [], retry: null, can_cancel: true, trace: [{ at: "2026-11-12T10:00:00+00:00", what: "GitHub reported that workflow run 501 failed on main." }], tokens: 4200,
      investigations: [{ id: "i1", title: "CI failure: x", status: "completed", ai: true, models: ["claude-sonnet-5-5"], tokens: 1200 }],
    },
    {
      id: "a2", event: "ci_failure", repo: "shop", title: "CI failed on dev", status: "skipped", reason: "Not the main branch (this rule watches only that branch)", link: "",
      created_at: new Date().toISOString(), files: [], reviews: [], investigations: [], retry: "run_anyway", can_cancel: false, trace: [], tokens: 0,
    },
    {
      id: "a3", event: "pr_review", repo: "shop", title: "PR #7: Fix the user lookup", status: "started", reason: "2 review(s) of 1 file(s): 1 finding(s)", link: "https://github.com/acme/shop/pull/7",
      created_at: new Date().toISOString(), files: ["app/auth.py"], skipped: [{ file: ".env", why: "it may hold a secret" }], investigations: [], retry: "review_again", can_cancel: false, trace: [], tokens: 0,
      reviews: [
        { id: "p1", persona: "developer", status: "done", model: "claude-sonnet-5-5", findings: 1, summary: "", ai: true },
        { id: "", persona: "security-auditor", status: "failed", model: "", findings: 0, summary: "The AI model could not write the review.", ai: false, attempts: 2 },
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
    expect(pr).toHaveTextContent("Nothing is posted unless you approve it");
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
    expect(puts[0].body).toEqual({ event: "pr_review", enabled: true, daily_limit: 8, all_branches: true, re_review: false });
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
    expect(runs).toHaveTextContent("Not read: .env (it may hold a secret)");
  });
});

describe("try again, run anyway, review again", () => {
  it("shows a failed review's reason, and a button on each run that can be started again", async () => {
    open();
    const runs = await screen.findByLabelText("Recent runs");
    await within(runs).findByText("CI failed on main");
    expect(runs).toHaveTextContent("failed: The AI model could not write the review.");
    expect(within(runs).getByRole("button", { name: "Run anyway" })).toBeInTheDocument();
    expect(within(runs).getByRole("button", { name: "Review again" })).toBeInTheDocument();
    expect(within(runs).getAllByRole("button", { name: /again|anyway/i })).toHaveLength(2); // the started CI run has none
  });

  it("starts a run again with one request", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method === "POST") { calls.push(u); return json({ id: "a3", status: "started" }); }
      if (u.endsWith("/automations")) return json(overview);
      return json({}, 404);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><MemoryRouter><AutomationPanel /></MemoryRouter></FeedbackProvider></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Review again" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatch(/\/automations\/runs\/a3\/retry$/);
  });

  it("saves the review new commits option on a pull request rule only", async () => {
    const puts = open();
    const pr = await screen.findByLabelText("Review new pull requests");
    const ci = screen.getByLabelText("Investigate CI failures");
    expect(within(ci).queryByLabelText(/Review again after new commits/)).toBeNull();
    await userEvent.click(within(pr).getByLabelText("Review again after new commits of Every repository"));
    await userEvent.click(within(pr).getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body).toEqual({ event: "pr_review", enabled: false, daily_limit: 5, all_branches: false, re_review: true });
  });
});

describe("controls: pause, token budget, cancel, and why a run happened", () => {
  it("shows what the agent used, what failed, and any problem", async () => {
    open();
    const c = await screen.findByLabelText("Controls");
    expect(c).toHaveTextContent("4,200");
    expect(c).toHaveTextContent("Failed runs in the last day: 1");
    expect(within(c).getByRole("alert")).toHaveTextContent("1 automatic run failed in the last day");
  });

  it("pauses the agent only after a confirmation, and resumes without one", async () => {
    const puts = open();
    const c = await screen.findByLabelText("Controls");
    await userEvent.click(within(c).getByRole("button", { name: /Pause the agent/ }));
    expect(puts).toHaveLength(0);
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].url).toMatch(/\/automations\/controls$/);
    expect(puts[0].body).toEqual({ paused: true, token_budget: 0 });
  });

  it("shows a paused agent and offers to resume it", async () => {
    const puts = open({ ...overview, controls: { ...overview.controls, paused: true } });
    const c = await screen.findByLabelText("Controls");
    expect(within(c).getByText("paused")).toBeInTheDocument();
    await userEvent.click(within(c).getByRole("button", { name: /Resume the agent/ }));
    await waitFor(() => expect(puts[0]?.body).toEqual({ paused: false, token_budget: 0 }));
  });

  it("saves a daily token budget", async () => {
    const puts = open();
    const c = await screen.findByLabelText("Controls");
    await userEvent.type(within(c).getByLabelText("Daily token budget"), "50000");
    await userEvent.click(within(c).getByRole("button", { name: "Save budget" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body).toEqual({ paused: false, token_budget: 50000 });
  });

  it("cancels a run that is going on, after a confirmation", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (init?.method === "POST") { calls.push(u); return json({ id: "a1", status: "cancelled" }); }
      if (u.endsWith("/automations")) return json(overview);
      return json({}, 404);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><MemoryRouter><AutomationPanel /></MemoryRouter></FeedbackProvider></QueryClientProvider>);
    const runs = await screen.findByLabelText("Recent runs");
    await within(runs).findByText("CI failed on main");
    expect(within(runs).getAllByRole("button", { name: "Cancel" })).toHaveLength(1); // only the run that is going on
    await userEvent.click(within(runs).getByRole("button", { name: "Cancel" }));
    expect(calls).toHaveLength(0);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatch(/\/automations\/runs\/a1\/cancel$/);
  });

  it("shows why a run happened, and what it used", async () => {
    open();
    const runs = await screen.findByLabelText("Recent runs");
    await within(runs).findByText("CI failed on main");
    expect(runs).toHaveTextContent("4,200 tokens");
    expect(within(runs).getByText("Why it ran, and what it did")).toBeInTheDocument();
    expect(runs).toHaveTextContent("GitHub reported that workflow run 501 failed on main.");
  });

  it("has a switch for stale pages and for the nightly watch, without branch options", async () => {
    open();
    const drift = await screen.findByLabelText("Draft updates for stale pages");
    const watch = screen.getByLabelText("Nightly watch");
    expect(within(drift).getByLabelText("Draft updates for stale pages of shop")).not.toBeChecked();
    expect(within(drift).queryByLabelText(/Every branch/)).toBeNull();
    expect(within(watch).queryByLabelText(/Daily limit/)).toBeNull();
    expect(watch).toHaveTextContent("no model is used");
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
    expect(how).toHaveTextContent("nothing reaches GitHub unless an admin posts a drafted comment");
    expect(how).toHaveTextContent("stops if they are no longer an admin");
    expect(screen.queryByLabelText("Which model runs this")).toBeNull();  // no card: each run says which model wrote it
    expect(await screen.findByLabelText("Investigate failures of shop")).toBeChecked();
  });
});
