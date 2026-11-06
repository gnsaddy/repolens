import { useEffect, type ReactNode } from "react";
import { Footer, Nav, REPO_URL, SECURITY_MD_URL } from "@/components/LandingChrome";
import { useAuth } from "@/lib/auth";
import { useLocation } from "react-router-dom";

export { REPO_URL, SECURITY_MD_URL };

const LINKS = [
  { href: "/about", label: "About" },
  { href: "/security", label: "Security" },
  { href: "/faq", label: "FAQ" },
] as const;

/** The frame for pages anyone may read without signing in: About, Security and FAQ. Same bar, type and footer as the landing page. */
export default function PublicLayout({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  const { user } = useAuth();
  const { pathname, hash } = useLocation();
  // The router changes the address but does not scroll to a #section, so a link such as /security#report did nothing.
  useEffect(() => {
    if (!hash) return;
    const reduce = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [pathname, hash]);
  return (
    <div className="landing min-h-screen" id="top">
      <a href="#main" className="body-5 sr-only rounded-lg bg-brand px-3 py-2 text-white focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-40">Skip to content</a>
      <Nav links={LINKS} current={pathname} signIn={user ? "/" : "/login"} signInLabel={user ? "Open the app" : "Sign in"} />
      <main id="main" className="mx-auto max-w-[920px] px-4 pb-24 pt-24">
        <h1 className="heading-4 md:heading-3 text-l-text">{title}</h1>
        <p className="body-3 mt-4 max-w-[680px] text-l-muted">{intro}</p>
        <div className="mt-12 space-y-12">{children}</div>
      </main>
      <Footer />
    </div>
  );
}

export function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="scroll-mt-24">
      <h2 id={`${id}-h`} className="heading-6 text-l-text">{title}</h2>
      <div className="body-6 mt-4 space-y-4 text-l-muted">{children}</div>
    </section>
  );
}

export function Points({ items }: { items: (string | ReactNode)[] }) {
  return <ul className="list-disc space-y-2 pl-6">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>;
}
