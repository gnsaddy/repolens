import { Moon, Sun } from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import Logo from "@/components/Logo";
import { ISSUES_URL } from "@/components/ReportIssue";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { APP_VERSION } from "@/lib/version";

export const REPO_URL = ISSUES_URL.replace(/\/issues$/, "");
export const SECURITY_MD_URL = `${REPO_URL}/blob/main/SECURITY.md`;

export type NavLinkItem = { href: string; label: string };
/** In page anchors use a plain link; routes use the router so the page does not reload. */
function Anchor({ href, className, children, onClick, style }: { href: string; className?: string; children: ReactNode; onClick?: () => void; style?: React.CSSProperties }) {
  return href.startsWith("/") ? <Link to={href} className={className} onClick={onClick} style={style}>{children}</Link> : <a href={href} className={className} onClick={onClick} style={style}>{children}</a>;
}

function ThemeButton() {
  const theme = useTheme();
  return (
    <button type="button" onClick={theme.toggle} aria-label={theme.dark ? "Switch to day mode" : "Switch to night mode"} aria-pressed={theme.dark} title={theme.dark ? "Day mode" : "Night mode"}
      className="grid h-8 w-8 place-items-center rounded-lg text-l-muted transition-colors duration-300 ease-land hover:bg-l-sunk hover:text-l-text active:scale-[0.98]">
      {theme.dark ? <Sun size={20} aria-hidden /> : <Moon size={20} aria-hidden />}
    </button>
  );
}

export const linkBtn = "button-sm inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-white transition-colors duration-300 ease-land hover:bg-brand-600 active:scale-[0.98]";

/** A compact bar that floats off the top edge. On a phone the links open as a full height panel and arrive one after another. */
export function Nav({ links, signIn = "#sign-in", signInLabel = "Sign in", current }: { links: readonly NavLinkItem[]; signIn?: string; signInLabel?: string; current?: string }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", esc);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", esc); document.body.style.overflow = ""; };
  }, [open]);
  const line = "absolute left-0 h-0.5 w-5 rounded-full bg-current transition-transform duration-500 ease-land";
  const home = signIn.startsWith("#") ? "#top" : "/login";
  return (
    <>
      <header className="pointer-events-none fixed inset-x-0 top-4 z-30 flex justify-center px-4">
        <nav aria-label="Main" className="pointer-events-auto flex items-center gap-4 rounded-2xl border border-l-line bg-l-bg/70 p-2 backdrop-blur-md">
          <Anchor href={home} className="flex items-center gap-2 rounded-lg px-2"><Logo className="h-6 w-6" /><span className="body-4 text-l-text">RepoLens AI</span></Anchor>
          <ul className="hidden items-center gap-1 md:flex">
            {links.map((l) => <li key={l.href}><Anchor href={l.href} className={cn("body-5 rounded-lg px-3 py-2 transition-colors duration-300 ease-land hover:bg-l-sunk hover:text-l-text", current === l.href ? "bg-l-sunk text-l-text" : "text-l-muted")}>{l.label}</Anchor></li>)}
          </ul>
          <ThemeButton />
          <Anchor href={signIn} className={cn(linkBtn, "hidden sm:inline-flex")}>{signInLabel}</Anchor>
          <button type="button" className="grid h-8 w-8 place-items-center rounded-lg text-l-text md:hidden" aria-expanded={open} aria-controls="land-menu" aria-label={open ? "Close menu" : "Open menu"} onClick={() => setOpen(!open)}>
            <span className="relative block h-5 w-5" aria-hidden>
              <span className={cn(line, "top-1", open && "translate-y-1 rotate-45")} />
              <span className={cn(line, "top-3", open && "-translate-y-1 -rotate-45")} />
            </span>
          </button>
        </nav>
      </header>
      <div id="land-menu" aria-hidden={!open} inert={!open} className={cn("fixed inset-0 z-20 flex flex-col justify-center gap-4 bg-l-bg/95 p-8 backdrop-blur-xl transition-opacity duration-500 ease-land md:hidden", open ? "land-menu-open opacity-100" : "pointer-events-none opacity-0")}>
        {[...links, { href: signIn, label: signInLabel }].map((l, i) => (
          <Anchor key={l.href} href={l.href} onClick={() => setOpen(false)} style={{ transitionDelay: open ? `${Math.min(i * 60, 400)}ms` : "0ms" }} className="land-menu-link heading-5 text-l-text">{l.label}</Anchor>))}
      </div>
    </>
  );
}

function Column({ title, links }: { title: string; links: [string, string][] }) {
  return (
    <nav aria-label={title}>
      <h2 className="caption-3 text-l-muted">{title}</h2>
      <ul className="body-6 mt-3 space-y-2">{links.map(([href, label]) => (
        <li key={label}>{href.startsWith("/")
          ? <Link className="text-l-link hover:underline" to={href}>{label}</Link>
          : <a className="text-l-link hover:underline" href={href} target="_blank" rel="noopener noreferrer">{label}</a>}</li>))}</ul>
    </nav>
  );
}

/** One footer for the landing page and the public pages (About, Security, FAQ). */
export function Footer() {
  return (
    <footer className="border-t border-l-line bg-l-sunk px-4 py-12">
      <div className="mx-auto max-w-[1200px]">
        <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-[1.3fr_1fr_1fr_1fr_1fr]">
          <div>
            <div className="title-3 flex items-center gap-2 text-l-text"><Logo className="h-6 w-6" />RepoLens AI</div>
            <p className="caption-5 mt-3 text-l-muted">A living wiki for your code and docs. Open source, bring your own model, and built to be checked.</p>
            <Link to="/security#status" className="mt-4 inline-flex flex-col rounded-lg border border-l-line bg-l-card px-3 py-2 transition-colors duration-300 ease-land hover:bg-l-bg">
              <span className="caption-4 text-l-text">Independent security audit</span><span className="caption-5 text-l-muted">Not yet. See where each control stands.</span></Link>
          </div>
          <Column title="Product" links={[["/about", "About"], ["/faq", "FAQ"], [`${REPO_URL}/blob/main/docs/features.md`, "What it does"], [`${REPO_URL}/blob/main/changelog/README.md`, "Changelog"]]} />
          <Column title="Trust" links={[["/security", "Security"], ["/security#status", "Where each control stands"], ["/security#report", "Report a vulnerability"], ["/security#official", "Official channels"]]} />
          <Column title="Developers" links={[[REPO_URL, "Source code"], [`${REPO_URL}/blob/main/docs/quickstart.md`, "Quickstart"], [`${REPO_URL}/blob/main/CONTRIBUTING.md`, "Contribute"], [`${REPO_URL}/blob/main/docs/github-action.md`, "GitHub Action"]]} />
          <Column title="Community" links={[[`${ISSUES_URL}/new/choose`, "Report a bug or request a feature"], [SECURITY_MD_URL, "SECURITY.md"], [`${REPO_URL}/blob/main/CODE_OF_CONDUCT.md`, "Code of conduct"], [`${REPO_URL}/blob/main/LICENSE`, "Apache License 2.0"]]} />
        </div>
        <p className="caption-5 mt-8 border-t border-l-line pt-4 text-l-muted">RepoLens AI {APP_VERSION} · Apache License 2.0 · No telemetry, nothing sent to the authors</p>
      </div>
    </footer>
  );
}
