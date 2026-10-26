import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { AuthProvider } from "@/lib/auth";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

const respond = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
function open(route: string, signedIn = false) {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const u = String(input);
    if (u.endsWith("/auth/me")) return signedIn ? respond({ id: "1", email: "a@b.io", name: "Ana", role: "member", org_id: "o" }) : respond({ detail: "no" }, 401);
    return respond({});
  });
  if (signedIn) localStorage.setItem("repolens.token", "tok");
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter initialEntries={[route]}><App /></MemoryRouter></AuthProvider></QueryClientProvider>);
}

describe("Security page", () => {
  it("opens without signing in and lists exactly where data can go", async () => {
    open("/security");
    expect(await screen.findByRole("heading", { level: 1, name: "Security" })).toBeInTheDocument(); // not bounced to the sign-in page
    const table = screen.getByRole("table", { name: "Where data can go" });
    expect(within(table).getByText("A language model you choose")).toBeInTheDocument();
    expect(within(table).getByText(/Only if you connect a model/)).toBeInTheDocument();
    const authors = within(table).getByText("RepoLens' authors").closest("tr")!;
    expect(authors).toHaveTextContent("Nothing");
    expect(authors).toHaveTextContent("no analytics, usage ping or crash reporting");
  });

  it("covers access, secrets, the AI's limits, how to check it, honest limits and how to report a problem", async () => {
    open("/security");
    await screen.findByRole("heading", { level: 1, name: "Security" });
    for (const h of ["Where your data goes", "Who can do what", "Keeping secrets out", "The AI is not in charge", "A record of what happened", "Check it yourself", "Honest limits", "Reporting a problem", "Official channels"])
      expect(screen.getByRole("heading", { level: 2, name: h })).toBeInTheDocument();
    const limits = screen.getByRole("region", { name: "Honest limits" });
    expect(limits).toHaveTextContent(/not had an independent security audit and holds no compliance certification/);
    expect(limits).toHaveTextContent("You run it");
    const report = screen.getByRole("link", { name: /Report a vulnerability privately/ });
    expect(report).toHaveAttribute("href", expect.stringContaining("/security/advisories/new"));
    expect(report.getAttribute("rel")).toContain("noopener");
    expect(screen.getByRole("region", { name: "Reporting a problem" })).toHaveTextContent(/do not open a public issue/);
    expect(document.body.textContent).not.toMatch(/SOC ?2 (Type|compliant|certified)|ISO ?27001 (certified)|HIPAA compliant|GDPR compliant/i); // no certification is claimed
  });
});

describe("About page", () => {
  it("opens without signing in and says what it is, what it does and where it stands", async () => {
    open("/about");
    expect(await screen.findByRole("heading", { level: 1, name: "About RepoLens AI" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "What you can do with it" })).toHaveTextContent("DeepMind Wiki");
    expect(screen.getByRole("region", { name: "What you can do with it" })).toHaveTextContent("Suggested fixes");
    const status = screen.getByRole("region", { name: "Where it stands" });
    expect(status).toHaveTextContent("v0.1-beta");
    expect(status).toHaveTextContent(/not had an independent security audit/);
    expect(screen.getByRole("region", { name: "Who built it and how it is licensed" })).toHaveTextContent("Apache License 2.0");
  });
});

describe("how people reach them", () => {
  it("are one click from the sign-in page", async () => {
    open("/login");
    const nav = await screen.findByRole("navigation", { name: "More about RepoLens AI" });
    await userEvent.click(within(nav).getByRole("link", { name: "Security" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Security" })).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("navigation", { name: "Public pages" })).getByRole("link", { name: "About" }));
    expect(await screen.findByRole("heading", { level: 1, name: "About RepoLens AI" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Public pages" })).getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("stay readable while signed in, and offer the way back into the app", async () => {
    open("/security", true);
    expect(await screen.findByRole("heading", { level: 1, name: "Security" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Public pages" })).getByRole("link", { name: "Open the app" })).toHaveAttribute("href", "/");
  });
});
