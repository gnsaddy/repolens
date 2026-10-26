import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import Logo from "@/components/Logo";
import { ISSUES_URL } from "@/components/ReportIssue";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { APP_VERSION } from "@/lib/version";

export const REPO_URL = ISSUES_URL.replace(/\/issues$/, "");
export const SECURITY_MD_URL = `${REPO_URL}/blob/main/SECURITY.md`;

/** The frame for pages anyone may read without signing in: About and Security. */
export default function PublicLayout({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  const { user } = useAuth();
  const link = ({ isActive }: { isActive: boolean }) => cn("rounded-md px-3 py-1.5 text-sm", isActive ? "bg-white/15 text-white" : "text-slate-300 hover:bg-white/10");
  return (
    <div className="min-h-screen bg-white text-ink">
      <header className="bg-sidebar">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-3">
          <Link to={user ? "/" : "/login"} className="flex items-center gap-2 text-white" aria-label="RepoLens AI home"><Logo className="h-8 w-8" /><span className="font-semibold">RepoLens AI</span><span className="rounded bg-white/10 px-1.5 py-0.5 text-xs text-slate-300">{APP_VERSION}</span></Link>
          <nav aria-label="Public pages" className="ml-auto flex items-center gap-1">
            <NavLink to="/about" className={link}>About</NavLink>
            <NavLink to="/security" className={link}>Security</NavLink>
            <Link to={user ? "/" : "/login"} className="ml-2 rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">{user ? "Open the app" : "Sign in"}</Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-10">
        <h1 className="text-3xl font-semibold">{title}</h1>
        <p className="mt-2 max-w-3xl text-muted">{intro}</p>
        <div className="mt-8 space-y-10">{children}</div>
      </main>
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-5 text-xs text-muted">
          <span>RepoLens AI {APP_VERSION}, Apache License 2.0</span>
          <a className="text-brand hover:underline" href={REPO_URL} target="_blank" rel="noopener noreferrer">Source code</a>
          <a className="text-brand hover:underline" href={SECURITY_MD_URL} target="_blank" rel="noopener noreferrer">SECURITY.md</a>
          <a className="text-brand hover:underline" href={`${ISSUES_URL}/new/choose`} target="_blank" rel="noopener noreferrer">Report a bug or request a feature</a>
        </div>
      </footer>
    </div>
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
