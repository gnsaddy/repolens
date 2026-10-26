import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { AuthProvider } from "@/lib/auth";

beforeEach(() => localStorage.clear());
afterEach(() => { vi.restoreAllMocks(); document.documentElement.classList.remove("dark"); });

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
    expect(await within(screen.getByRole("navigation", { name: "Public pages" })).findByRole("link", { name: "Open the app" })).toHaveAttribute("href", "/"); // waits for the signed-in user to load
  });
});

describe("control status", () => {
  it("lists every control as built, partly or not yet, and does not round gaps up", async () => {
    open("/security");
    const table = await screen.findByRole("table", { name: "Control status" });
    const row = (name: RegExp) => within(table).getByText(name).closest("tr")!;
    expect(row(/^Roles and per-repository access$/)).toHaveTextContent("Built");
    expect(row(/^SAML single sign-on$/)).toHaveTextContent("Not yet");
    expect(row(/^Single sign-on \(OpenID Connect\)$/)).toHaveTextContent("Partly"); // it works, but only through the API
    expect(row(/^Encryption in transit/)).toHaveTextContent("Partly"); // we serve HTTP behind your proxy
    expect(row(/^SOC 2, ISO 27001/)).toHaveTextContent("Not yet");
    expect(row(/^Independent penetration test$/)).toHaveTextContent("Not yet");
    expect(within(table).getAllByText("Built").length).toBeGreaterThan(5);
    expect(within(table).getAllByText("Not yet").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByRole("heading", { level: 2, name: "Where each control stands" })).toBeInTheDocument();
  });
});

describe("FAQ page", () => {
  it("answers the trust questions without signing in, and is honest about the beta", async () => {
    open("/faq");
    expect(await screen.findByRole("heading", { level: 1, name: "Questions about trust" })).toBeInTheDocument();
    const faq = screen.getByLabelText("Frequently asked questions");
    for (const q of ["Is RepoLens safe to use?", "Does RepoLens send my code or documents anywhere?", "Do I need an AI model?", "Does RepoLens train on my data?", "What are the official channels", "How do I report a security problem?"])
      expect(within(faq).getByText(new RegExp(q.replace(/[?()]/g, "\\$&")))).toBeInTheDocument();
    expect(faq).toHaveTextContent(/no independent security audit yet/);
    expect(faq).toHaveTextContent(/does not train models/);
    expect(faq).toHaveTextContent("github.com/gnsaddy/repolens");
    expect(document.body.textContent).not.toMatch(/SOC ?2 (Type|compliant|certified)|ISO ?27001 certified|HIPAA compliant|GDPR compliant/i);
  });

  it("opens and closes an answer, and is linked from the sign-in page, the navigation and the footer", async () => {
    open("/login");
    await userEvent.click(within(await screen.findByRole("navigation", { name: "More about RepoLens AI" })).getByRole("link", { name: "FAQ" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Questions about trust" })).toBeInTheDocument();
    const second = screen.getByText("Does RepoLens send my code or documents anywhere?").closest("details")!;
    expect(second.open).toBe(false);
    await userEvent.click(screen.getByText("Does RepoLens send my code or documents anywhere?"));
    expect(second.open).toBe(true);
    expect(within(screen.getByRole("navigation", { name: "Public pages" })).getByRole("link", { name: "FAQ" })).toHaveAttribute("href", "/faq");
    expect(within(screen.getByRole("navigation", { name: "Product" })).getByRole("link", { name: "FAQ" })).toHaveAttribute("href", "/faq");
  });
});

describe("footer", () => {
  it("groups the trust, developer and community links and states the audit position", async () => {
    open("/about");
    await screen.findByRole("heading", { level: 1, name: "About RepoLens AI" });
    const trust = screen.getByRole("navigation", { name: "Trust" });
    expect(within(trust).getByRole("link", { name: "Where each control stands" })).toHaveAttribute("href", "/security#status");
    expect(within(trust).getByRole("link", { name: "Report a vulnerability" })).toHaveAttribute("href", "/security#report");
    const dev = screen.getByRole("navigation", { name: "Developers" });
    expect(within(dev).getByRole("link", { name: "Contribute" })).toHaveAttribute("href", expect.stringContaining("/blob/main/CONTRIBUTING.md"));
    expect(within(dev).getByRole("link", { name: "Contribute" }).getAttribute("rel")).toContain("noopener");
    expect(screen.getByText("Independent security audit").closest("a")).toHaveTextContent(/Not yet/);
    expect(screen.getByText(/No telemetry, nothing sent to the authors/)).toBeInTheDocument();
  });
});

describe("day and night mode before signing in", () => {
  it("can be switched on the sign-in page, the public pages and the reset page, and is remembered", async () => {
    document.documentElement.classList.remove("dark");
    open("/login");
    await userEvent.click(await screen.findByRole("button", { name: "Switch to night mode" }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("repolens.theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to day mode" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(screen.getByRole("navigation", { name: "More about RepoLens AI" })).getByRole("link", { name: "About" }));
    await screen.findByRole("heading", { level: 1, name: "About RepoLens AI" });
    expect(document.documentElement.classList.contains("dark")).toBe(true); // the choice follows you to the next page
    await userEvent.click(screen.getByRole("button", { name: "Switch to day mode" })); // the same switch in the public header
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem("repolens.theme")).toBe("light");
  });

  it("is on the reset page too", async () => {
    open("/reset?token=abc");
    expect(await screen.findByRole("button", { name: /Switch to (night|day) mode/ })).toBeInTheDocument();
  });
});
