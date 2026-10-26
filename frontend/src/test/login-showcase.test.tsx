import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import Login from "@/pages/Login";

afterEach(() => vi.restoreAllMocks());

function show() {
  vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(new Response(JSON.stringify({}), { status: 401, headers: { "Content-Type": "application/json" } })));
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><MemoryRouter><Login /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("sign-in page", () => {
  it("says what you can do and why you can trust it, and links to the proof", async () => {
    show();
    const about = await screen.findByLabelText("About RepoLens AI");
    expect(within(about).getByRole("heading", { name: "A living wiki for your code and docs." })).toBeInTheDocument();
    const can = within(about).getByLabelText("What you can do");
    for (const t of ["Ask your docs and code", "Catch stale pages", "See how it connects", "Find the root cause"]) expect(can).toHaveTextContent(t);
    const trust = within(about).getByLabelText("Why you can trust it");
    expect(trust).toHaveTextContent("Your data stays on your server");
    expect(trust).toHaveTextContent("People decide, not the AI");
    expect(trust).toHaveTextContent("no usage data to its authors");
    expect(within(trust).getByRole("link", { name: /How we think about security/ })).toHaveAttribute("href", "/security"); // an in-app page, readable before signing in
    expect(within(trust).getByRole("link", { name: /About RepoLens AI/ })).toHaveAttribute("href", "/about");
    expect(within(trust).getByRole("link", { name: /Read the source/ })).toHaveAttribute("href", expect.stringMatching(/github\.com\/gnsaddy\/repolens$/));
  });

  it("is honest about what it is not", async () => {
    show();
    const trust = await screen.findByLabelText("Why you can trust it");
    expect(trust).toHaveTextContent(/v0\.2-beta release/);
    expect(trust).toHaveTextContent(/not had an independent security audit or a compliance certification/);
    expect(trust).not.toHaveTextContent(/SOC ?2|ISO ?27001|HIPAA|GDPR/i); // no certification is claimed
  });

  it("keeps the sign-in form first, and shows a short trust note where the panel does not fit", async () => {
    show();
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(document.body.textContent).toContain("Your data stays on your server and nothing is sent to an AI service unless you connect one.");
  });

  it("draws the flowing graph illustration and describes it for screen readers", async () => {
    show();
    const about = await screen.findByLabelText("About RepoLens AI");
    const art = within(about).getByRole("img", { name: /links flowing outward/ });
    expect(art.querySelectorAll("line.rl-flow").length).toBeGreaterThan(3);
  });
});
