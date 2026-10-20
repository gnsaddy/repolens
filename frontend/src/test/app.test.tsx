import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { ConfidenceBar } from "@/components/ui";
import { AuthProvider } from "@/lib/auth";
import { fmtCost, fmtTokens, pct, timeAgo } from "@/lib/utils";
import InvestigationView from "@/pages/InvestigationView";

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function wrap(ui: React.ReactNode, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AuthProvider><MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter></AuthProvider></QueryClientProvider>);
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("utils", () => {
  it("formats tokens, cost, percentages", () => {
    expect(fmtTokens(2_400_000)).toBe("2.40M");
    expect(fmtTokens(1500)).toBe("1.5K");
    expect(fmtCost(8.4231)).toBe("$8.42");
    expect(fmtCost(0.0105)).toBe("$0.0105");
    expect(pct(0.892)).toBe("89%");
    expect(pct(null)).toBe("—");
    expect(timeAgo(new Date(Date.now() - 5 * 60_000).toISOString())).toBe("5 min ago");
  });
});

describe("ConfidenceBar", () => {
  it("exposes the value accessibly", () => {
    render(<ConfidenceBar value={0.92} />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "92");
  });
});

describe("auth", () => {
  it("redirects anonymous users to the login page", async () => {
    wrap(<App />);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("logs in and shows the dashboard", async () => {
    const user = { id: "1", email: "a@b.io", name: "Alex", role: "admin", org_id: "o" };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) return json({ access_token: "tok", token_type: "bearer", user });
      if (url.endsWith("/dashboard")) return json({ repositories: 2, indexed_repositories: 2, investigations: 3, completed: 3, verified_accuracy: 0.5, reviewed: 2, avg_duration_s: 1.2, recent: [], top_repositories: [] });
      return json({}, 404);
    });
    wrap(<App />, "/login");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.io");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/Welcome back, Alex/)).toBeInTheDocument();
    expect(localStorage.getItem("repolens.token")).toBe("tok");
    await waitFor(() => expect(screen.getByText("50%")).toBeInTheDocument());
  });

  it("shows API errors on bad credentials", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ detail: "Invalid email or password" }, 401));
    wrap(<App />, "/login");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.io");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });
});

describe("InvestigationView", () => {
  const inv = {
    id: "abcdef123456", title: "Null pointer in user service", status: "completed", confidence: 0.85, root_cause: "Most likely located in src/user_profile.py:10",
    repo_ids: [], jira_issue_key: null, created_at: new Date().toISOString(), finished_at: null, query: "q", root_cause_status: "unconfirmed",
    suggested_fix: "Restore the null guard", report_md: "## RepoLens AI — Root Cause Analysis\n\n**Root cause:** Not yet confirmed", error: null, cache_hit: false,
    timeline: [{ step: "planner", agent: "planner", summary: "3 search terms", duration_ms: 4, at: "" }],
    hypotheses: [{ id: "h1", rank: 1, title: "Change in `get_profile`", explanation: "appears in the stack trace", confidence: 0.85, status: "supported",
      evidence: [{ type: "code", repo: "user-service", file: "src/user_profile.py", symbol: "get_profile", start_line: 8, end_line: 10, snippet: "def get_profile():\n    pass", provenance: "retrieval" }] }],
    evidence: { duration_s: 1.4 }, verification: { valid: true, checked_evidence: 2, problems: [], note: "checked" },
  };
  const user = { id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" };

  it("renders root cause, confidence and lets the user switch tabs", async () => {
    localStorage.setItem("repolens.token", "tok");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).endsWith("/auth/me") ? json(user) : json(inv));
    wrap(<Routes><Route path="/investigations/:id" element={<InvestigationView />} /></Routes>, "/investigations/abcdef123456");
    expect(await screen.findByText("Null pointer in user service")).toBeInTheDocument();
    expect(screen.getByText(/Most likely located/)).toBeInTheDocument();
    expect(screen.getByText("2 citations checked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirm/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Evidence" }));
    expect(await screen.findByText(/def get_profile/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(screen.getByText("3 search terms")).toBeInTheDocument();
  });
});
