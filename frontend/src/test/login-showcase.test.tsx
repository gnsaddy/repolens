import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import Login from "@/pages/Login";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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
    expect(trust).toHaveTextContent(/v\d+\.\d+\.\d+ release/);
    expect(trust).toHaveTextContent(/not had an independent security audit or a compliance certification/);
    expect(trust).not.toHaveTextContent(/SOC ?2|ISO ?27001|HIPAA|GDPR/i); // no certification is claimed
  });

  it("keeps the sign in form on the first screen, with one primary action and a skip link", async () => {
    show();
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
    expect(document.getElementById("sign-in")).toContainElement(screen.getByLabelText("Email"));
  });

  it("explains how it works and answers the usual questions, without hyphens in the copy", async () => {
    show();
    const how = await screen.findByLabelText("How it works");
    for (const t of ["Add your sources", "Ask or investigate", "Review the evidence"]) expect(how).toHaveTextContent(t);
    const faq = screen.getByLabelText("Questions and answers");
    expect(faq.querySelectorAll("details").length).toBeGreaterThanOrEqual(6);
    expect(faq).toHaveTextContent("Do I need an AI model?");
    const about = screen.getByLabelText("About RepoLens AI");
    expect(about.textContent).not.toMatch(/\w-\w/); // the landing page copy has no hyphens inside words
  });

  it("explains how Ask works, what repo intelligence covers, the benefits and best practices", async () => {
    show();
    const ask = await screen.findByLabelText("How Ask works");
    for (const t of ["You ask", "It searches", "It checks the sources", "You get an answer you can verify"]) expect(ask).toHaveTextContent(t);
    expect(ask).toHaveTextContent("Illustration of the answer format");
    const intel = screen.getByLabelText("Repo intelligence");
    for (const t of ["Knowledge graph", "Blast radius", "Change risk", "Who to ask", "Stale docs", "Root cause"]) expect(intel).toHaveTextContent(t);
    expect(intel).toHaveTextContent("(illustration)"); // the sample bars and graph say they are not real data
    expect(screen.getByLabelText("How you benefit")).toHaveTextContent("You stay in control");
    expect(screen.getByLabelText("Best practices").querySelectorAll("li").length).toBe(6);
  });

  it("shows the tagline in full when the visitor asks for less motion", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: /reduce/.test(q), media: q, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));
    show();
    const idea = await screen.findByLabelText("The idea");
    expect(idea).toHaveTextContent("Every answer shows the file and line it came from.");
    expect(idea.querySelectorAll(".land-word.is-on").length).toBe(idea.querySelectorAll(".land-word").length);
  });

  it("draws the flowing graph illustration and describes it for screen readers", async () => {
    show();
    const about = await screen.findByLabelText("About RepoLens AI");
    const art = within(about).getByRole("img", { name: /links flowing outward/ });
    expect(art.querySelectorAll("line.rl-flow").length).toBeGreaterThan(3);
  });
});
