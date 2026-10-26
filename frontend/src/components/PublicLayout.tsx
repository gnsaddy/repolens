import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import Logo from "@/components/Logo";
import ThemeToggle from "@/components/ThemeToggle";
import { ISSUES_URL } from "@/components/ReportIssue";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { APP_VERSION } from "@/lib/version";

export const REPO_URL = ISSUES_URL.replace(/\/issues$/, "");
export const SECURITY_MD_URL = `${REPO_URL}/blob/main/SECURITY.md`;

/** The frame for pages anyone may read without signing in: About and Security. */
export default function PublicLayout({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  const { user } = useAuth();
  const link = ({ isActive }: { isActive: boolean }) => cn("rounded-md px-3 py-1.5 text-sm", isActive ? "bg-slate-100 text-ink" : "text-slate-600 hover:bg-slate-100");
  return (
    <div className="min-h-screen bg-white text-ink">
      <header className="border-b border-line bg-slate-50">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-3">
          <Link to={user ? "/" : "/login"} className="flex items-center gap-2 text-ink" aria-label="RepoLens AI home"><Logo className="h-8 w-8" /><span className="font-semibold">RepoLens AI</span><span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{APP_VERSION}</span></Link>
          <nav aria-label="Public pages" className="ml-auto flex items-center gap-1">
            <NavLink to="/about" className={link}>About</NavLink>
            <NavLink to="/security" className={link}>Security</NavLink>
            <NavLink to="/faq" className={link}>FAQ</NavLink>
            <ThemeToggle />
            <Link to={user ? "/" : "/login"} className="ml-2 rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">{user ? "Open the app" : "Sign in"}</Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-10">
        <h1 className="text-3xl font-semibold">{title}</h1>
        <p className="mt-2 max-w-3xl text-muted">{intro}</p>
        <div className="mt-8 space-y-10">{children}</div>
      </main>
      <footer className="border-t border-line bg-slate-50">
        <div className="mx-auto max-w-5xl px-4 py-8">
          <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-[1.3fr_1fr_1fr_1fr_1fr]">
            <div>
              <div className="flex items-center gap-2 font-semibold"><Logo className="h-6 w-6" />RepoLens AI</div>
              <p className="mt-2 text-xs text-muted">A living wiki for your code and docs. Open source, bring your own model, and built to be checked.</p>
              <Link to="/security#status" className="mt-3 inline-flex flex-col rounded-md border border-dashed border-line px-3 py-2 text-xs hover:bg-white">
                <span className="font-medium">Independent security audit</span><span className="text-muted">Not yet. See where each control stands.</span></Link>
            </div>
            <FooterColumn title="Product" links={[["/about", "About"], ["/faq", "FAQ"], [`${REPO_URL}/blob/main/docs/features.md`, "What it does"], [`${REPO_URL}/blob/main/changelog/README.md`, "Changelog"]]} />
            <FooterColumn title="Trust" links={[["/security", "Security"], ["/security#status", "Where each control stands"], ["/security#report", "Report a vulnerability"], ["/security#official", "Official channels"]]} />
            <FooterColumn title="Developers" links={[[REPO_URL, "Source code"], [`${REPO_URL}/blob/main/docs/quickstart.md`, "Quickstart"], [`${REPO_URL}/blob/main/CONTRIBUTING.md`, "Contribute"], [`${REPO_URL}/blob/main/docs/github-action.md`, "GitHub Action"]]} />
            <FooterColumn title="Community" links={[[`${ISSUES_URL}/new/choose`, "Report a bug or request a feature"], [SECURITY_MD_URL, "SECURITY.md"], [`${REPO_URL}/blob/main/CODE_OF_CONDUCT.md`, "Code of conduct"], [`${REPO_URL}/blob/main/LICENSE`, "Apache License 2.0"]]} />
          </div>
          <p className="mt-8 border-t border-line pt-4 text-xs text-muted">RepoLens AI {APP_VERSION} · Apache License 2.0 · No telemetry, nothing sent to the authors</p>
        </div>
      </footer>
    </div>
  );
}

function FooterColumn({ title, links }: { title: string; links: [string, string][] }) {
  return (
    <nav aria-label={title}>
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
      <ul className="mt-2 space-y-1.5 text-sm">{links.map(([href, label]) => (
        <li key={label}>{href.startsWith("/")
          ? <Link className="text-brand hover:underline" to={href}>{label}</Link>
          : <a className="text-brand hover:underline" href={href} target="_blank" rel="noopener noreferrer">{label}</a>}</li>))}</ul>
    </nav>
  );
}

export function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="scroll-mt-6">
      <h2 id={`${id}-h`} className="text-xl font-semibold">{title}</h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-slate-700">{children}</div>
    </section>
  );
}

export function Points({ items }: { items: (string | ReactNode)[] }) {
  return <ul className="list-disc space-y-1.5 pl-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>;
}
