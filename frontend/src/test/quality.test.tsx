import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedbackProvider } from "@/components/feedback";
import QualityPanel from "@/components/QualityPanel";
import type { GoldenOverview, GoldenResult } from "@/lib/api";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const bad: GoldenResult = { id: "q2", question: "How does a user get assigned a role?", passed: false, files: ["api/other.py"], answer: "", checks: [{ name: "finds api/auth.py", ok: false, detail: "not among the sources used" }] };
const good: GoldenResult = { id: "q1", question: "Where is get_profile defined?", passed: true, files: ["svc/user_profile.py"], answer: "", checks: [{ name: "finds user_profile.py", ok: true, detail: "" }] };
const overview = (o: Partial<GoldenOverview> = {}): GoldenOverview => ({
  questions: [
    { id: "q1", question: good.question, expect_files: ["user_profile.py"], expect_phrases: [], repo_ids: [], mine: true, last: good },
    { id: "q2", question: bad.question, expect_files: ["api/auth.py"], expect_phrases: ["authorize"], repo_ids: [], mine: true, last: bad }],
  runs: [], active: null, max_full: 20, changed: [],
  latest: { id: "r2", mode: "retrieval", trigger: "manual", status: "completed", total: 2, done: 2, passed: 1, error: null, started_at: new Date().toISOString(), finished_at: new Date().toISOString(), results: [good, bad] },
  compare: { delta: -0.5, broke: ["Where is the retry policy?"], fixed: [] }, ...o,
});

function open(data: GoldenOverview, canWrite = true) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET";
    if (method !== "GET") calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/golden/run")) return json({ id: "r3", mode: "full", status: "running", total: 2, done: 0, passed: 0, error: null, started_at: "", finished_at: null }, 202);
    if (url.endsWith("/golden/questions") && method === "POST") return json({ id: "new" }, 201);
    if (url.endsWith("/golden")) return json(data);
    return json({});
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FeedbackProvider><MemoryRouter><QualityPanel canWrite={canWrite} /></MemoryRouter></FeedbackProvider></QueryClientProvider>);
  return calls;
}

describe("Quality checks", () => {
  it("shows the pass rate, what got worse and what failed with the reason", async () => {
    open(overview());
    const res = await screen.findByLabelText("Latest result");
    expect(res).toHaveTextContent("50%");
    expect(res).toHaveTextContent("1 of 2 questions passed");
    expect(res).toHaveTextContent("-50 points since the run before");
    expect(res).toHaveTextContent("Got worse:");
    expect(res).toHaveTextContent("Where is the retry policy?");
    expect(res).toHaveTextContent("finds api/auth.py: not among the sources used");
    expect(res).toHaveTextContent("Found instead: api/other.py");
    expect(res).toHaveTextContent("Retrieval runs check which files were found");
  });

  it("says when the last run started by itself after an index finished", async () => {
    const o = overview();
    open({ ...o, latest: { ...o.latest!, trigger: "reindex" } });
    expect(await screen.findByLabelText("Latest result")).toHaveTextContent("retrieval run, ran by itself after indexing");
  });

  it("says what changed since the last run", async () => {
    open(overview({ changed: ["the skills", "the indexed code or documents"] }));
    expect(await screen.findByRole("status")).toHaveTextContent("the skills and the indexed code or documents changed");
  });

  it("starts a retrieval or a full run", async () => {
    const calls = open(overview());
    await userEvent.click(await screen.findByRole("button", { name: "Run the checks" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/golden/run"))?.body).toEqual({ mode: "retrieval" }));
    await userEvent.selectOptions(screen.getByLabelText("Kind of run"), "full");
    expect(screen.getByText(/one model call each/)).toBeInTheDocument();
  });

  it("shows progress while a run is going and disables starting another", async () => {
    open(overview({ active: { id: "r9", mode: "full", trigger: "manual", status: "running", total: 4, done: 1, passed: 1, error: null, started_at: "", finished_at: null } }));
    expect(await screen.findByText(/Running: 1 of 4 questions/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
  });

  it("adds a question with the files and words a good answer needs", async () => {
    const calls = open(overview({ questions: [], latest: null, compare: { delta: null, broke: [], fixed: [] } }));
    expect(await screen.findByText(/None yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run the checks" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Add a question/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.type(within(dlg).getByLabelText("Question"), "How does a user get assigned a role?");
    await userEvent.type(within(dlg).getByLabelText("Expected files"), "api/auth.py{enter}policy.py");
    await userEvent.type(within(dlg).getByLabelText("Expected phrases"), "authorize");
    await userEvent.click(within(dlg).getByRole("button", { name: "Save question" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/golden/questions"))?.body).toEqual({ question: "How does a user get assigned a role?", expect_files: ["api/auth.py", "policy.py"], expect_phrases: ["authorize"] }));
  });

  it("hides the buttons from people who cannot change anything", async () => {
    open(overview(), false);
    await screen.findByLabelText("Latest result");
    expect(screen.queryByRole("button", { name: /Add a question/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run the checks" })).toBeNull();
  });
});
