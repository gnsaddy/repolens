import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import Investigations from "@/pages/Investigations";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

describe("Investigations from a CI log", () => {
  it("previews failures, preselects the first three, and starts the chosen ones", async () => {
    const started: unknown[] = [];
    const fails = Array.from({ length: 4 }, (_, i) => ({ index: i, name: `test_${i}`, kind: "pytest", message: `boom ${i}`, details: "", file: "t.py", line: i + 1, count: i === 1 ? 2 : 1 }));
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.endsWith("/investigations/ci/parse")) return json({ format: "pytest", failures: fails });
      if (u.endsWith("/investigations/ci/start")) { started.push(JSON.parse(String(init?.body))); return json({ started: [{ id: "i1", title: "x" }] }, 202); }
      return json([]);
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter><Investigations /></MemoryRouter></AuthProvider></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("tab", { name: "From a CI log" }));
    await userEvent.type(screen.getByLabelText("CI log"), "FAILED t.py::test_0");
    await userEvent.click(screen.getByRole("button", { name: "Find failures" }));
    expect(await screen.findByText(/4 failure\(s\) found in pytest/)).toBeInTheDocument();
    expect(screen.getByLabelText("Investigate test_3")).not.toBeChecked();
    expect(screen.getByText("×2")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Investigate test_2")); // untick one of the three
    await userEvent.click(screen.getByRole("button", { name: "Investigate 2 failures" }));
    await waitFor(() => expect(started).toHaveLength(1));
    expect(started[0]).toMatchObject({ log: "FAILED t.py::test_0", indexes: [0, 1] });
  });
});
