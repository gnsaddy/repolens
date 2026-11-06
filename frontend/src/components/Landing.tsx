import "@fontsource-variable/geist";
import {
  ArrowRight, ArrowsClockwise, BookOpenText, Bug, CaretDown, ChatCircleText, ClipboardText, ClockCounterClockwise, FileMagnifyingGlass, FolderOpen, GitPullRequest, Graph, Key,
  ListChecks, Lock, MagnifyingGlass, Paperclip, Question, Scroll, SealCheck, ShieldCheck, Sparkle, Stack, Target, UserCheck, UsersThree, Warning,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ElementType, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Footer, Nav, REPO_URL } from "@/components/LandingChrome";
import { cn } from "@/lib/utils";
import { APP_VERSION } from "@/lib/version";


const NAV = [
  { href: "#features", label: "Features" },
  { href: "#ask", label: "How Ask works" },
  { href: "#intelligence", label: "Repo intelligence" },
  { href: "#trust", label: "Trust" },
  { href: "#faq", label: "FAQ" },
] as const;

const CAN_DO = [
  { icon: Sparkle, title: "Ask your docs and code", text: "Answers cite the exact file and line. Search needs no model." },
  { icon: ClockCounterClockwise, title: "Catch stale pages", text: "When code changes, the wiki pages that describe it are flagged for review." },
  { icon: Graph, title: "See how it connects", text: "A graph of calls, links and mentions, with facts kept apart from guesses." },
  { icon: MagnifyingGlass, title: "Find the root cause", text: "From a bug report or CI log to ranked suspects, with the evidence shown." },
] as const;

const STEPS = [
  { icon: FolderOpen, title: "Add your sources", text: "Connect a repository or add documents. RepoLens indexes them on your server and masks keys and tokens first." },
  { icon: ChatCircleText, title: "Ask or investigate", text: "Ask a question, paste a bug report or a CI log, or open the graph to see what depends on what." },
  { icon: SealCheck, title: "Review the evidence", text: "Every answer links to its source. Fixes and wiki edits stay drafts until a person approves them." },
] as const;

const ASK_STEPS = [
  { icon: Question, title: "You ask", text: "In plain words, in the Ask chat, on the Knowledge page or from your editor." },
  { icon: MagnifyingGlass, title: "It searches", text: "Text, meaning, and the names and paths in your code, then follows the graph to neighbours." },
  { icon: ListChecks, title: "It checks the sources", text: "If the best sources miss the question's key words, it searches once more, wider." },
  { icon: BookOpenText, title: "You get an answer you can verify", text: "Numbered citations open the file and line. Without a model you get the best passages, labelled as extracted." },
] as const;

const INTEL = [
  { icon: Graph, title: "Knowledge graph", text: "Files, functions, documents and commits, joined by calls, imports, tests and mentions. Every link says how it was found." },
  { icon: Target, title: "Blast radius", text: "Pick a change and see the code that depends on it, the tests to run and the docs to update, across repositories." },
  { icon: Warning, title: "Change risk", text: "A score from 0 to 100 from seven signals, with the evidence behind each one, for a commit, a branch or a pull request." },
  { icon: UsersThree, title: "Who to ask", text: "Ownership from commit history, with a bus factor for each area and a flag when one person holds it all." },
  { icon: FileMagnifyingGlass, title: "Stale docs", text: "When code changes, the pages that describe it are flagged, and history pages such as changelogs are left alone." },
  { icon: Bug, title: "Root cause", text: "From a bug report or CI log to ranked suspects, with the code, commits and pull requests behind each." },
] as const;

const SIGNALS = [["Size of the change", 0.62], ["Dependency centrality", 0.85], ["Public interfaces", 0.4], ["Historical defects", 0.55], ["Test gap", 0.72], ["Sensitive paths", 0.3], ["Similar past changes", 0.48]] as const;

const BENEFITS = [
  { icon: Sparkle, title: "Answers without interrupting a colleague", text: "A new teammate can ask where something lives and get the file and line, not a guess." },
  { icon: Target, title: "Know what a change can break", text: "See the dependents, the tests to run and the docs to update before you merge." },
  { icon: ClockCounterClockwise, title: "Docs that stay honest", text: "Pages that describe changed code are flagged, so the wiki does not drift quietly." },
  { icon: Bug, title: "Debugging that shows its work", text: "Suspects come ranked, each with the evidence, so you can disagree with them." },
  { icon: UserCheck, title: "You stay in control", text: "Fixes, reports and wiki edits are drafts. Nothing is merged for you." },
  { icon: Lock, title: "Your code stays home", text: "It runs on your server, and nothing goes to an AI service unless you connect one." },
] as const;

const PRACTICES = [
  { icon: MagnifyingGlass, title: "Ask with real names", text: "Mention the file, function or error. Questions with real names find real code." },
  { icon: Stack, title: "Narrow the scope", text: "Limit a chat to one repository when you know where to look." },
  { icon: Paperclip, title: "Attach the log", text: "For an error, attach the .txt or .log file. Its stack frames and names are used to search your code." },
  { icon: ArrowsClockwise, title: "Keep the index fresh", text: "Connect the push webhook, or index again after large changes, so answers match the code." },
  { icon: ListChecks, title: "Save the questions that matter", text: "Turn important questions into quality checks and run them again after every change." },
  { icon: SealCheck, title: "Read the evidence before you approve", text: "Open the citations behind a draft. Approving it is your decision, not the AI's." },
] as const;

// Every line here is something the code does and a test or a document backs. Keep it that way: edit this list only with the code.
const TRUST = [
  { icon: Lock, title: "Your data stays on your server", text: "No AI service is contacted unless you connect one, and Settings shows exactly what leaves." },
  { icon: UserCheck, title: "People decide, not the AI", text: "Fixes, reports and wiki edits are drafts until someone approves them. Nothing is merged for you." },
  { icon: Key, title: "Access you control", text: "Roles, access per repository, sign in with an authenticator app, single sign on and an audit log." },
  { icon: ShieldCheck, title: "Secrets are masked", text: "Keys and tokens are stripped before text is indexed or sent to any model you choose." },
  { icon: Scroll, title: "Open source, no tracking", text: "Apache 2.0. RepoLens sends no usage data to its authors, so you can read what the code does." },
] as const;

const FAQ = [
  { q: "Do I need an AI model?", a: "No. Indexing, search, the graph and the stale page checks work without one. Connect a model of your choice to get written answers." },
  { q: "Where does my data live?", a: "On your server, in PostgreSQL, or in SQLite for a local trial. Nothing is sent to an AI service unless you connect one, and Settings shows exactly what leaves." },
  { q: "What happens to secrets in my code?", a: "Keys and tokens are masked before text is indexed or sent to any model you choose." },
  { q: "Can the AI change my code?", a: "No. Fixes, reports and wiki edits are drafts until a person approves them. Nothing is merged for you." },
  { q: "Who can see which repositories?", a: "People are viewers, members or admins, and access can be set per repository. Admins can read the audit log." },
  { q: "Has it been audited?", a: `No. This is a ${APP_VERSION} release without an independent security audit or a compliance certification. The code is open, so you can read it.` },
  { q: "How do I run it?", a: "With Docker. The quick start in the README needs one command and nothing else installed." },
  { q: "What does it cost?", a: "Nothing. It is open source under the Apache 2.0 license and runs on your own server." },
] as const;

const TAGLINE = [
  "Every answer shows the file and line it came from.",
  "Every fix waits for a person to approve it.",
  "Nothing leaves your server unless you connect it.",
];

const reduced = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** True once the element has scrolled into view (and stays true). Without IntersectionObserver it is simply true. */
function useInView<T extends HTMLElement>(margin = "0px 0px -10% 0px") {
  const ref = useRef<T | null>(null);
  const [shown, setShown] = useState(() => typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const el = ref.current;
    if (shown || !el) return;
    const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) { setShown(true); io.disconnect(); } }, { rootMargin: margin });
    io.observe(el);
    return () => io.disconnect();
  }, [shown, margin]);
  return [ref, shown] as const;
}

/** Fades and lifts a block into place the first time it scrolls into view. */
function Reveal({ children, className, as: Tag = "div" }: { children: ReactNode; className?: string; as?: ElementType }) {
  const [ref, shown] = useInView<HTMLElement>();
  return <Tag ref={ref} className={cn("land-reveal", shown && "is-in", className)}>{children}</Tag>;
}

/** The Ask pipeline: four steps light up one after another, and a dot travels along the line between them. */
function AskFlow() {
  const [ref, shown] = useInView<HTMLDivElement>("0px 0px -20% 0px");
  return (
    <div ref={ref} className={cn("land-flow", shown && "is-in")}>
      <ol className="relative grid gap-6 md:grid-cols-4">
        <span aria-hidden className="absolute left-0 right-0 top-5 hidden h-0.5 bg-l-line md:block"><i className="land-flow-dot absolute inset-0 block rounded-full bg-brand" /></span>
        {ASK_STEPS.map(({ icon: Icon, title, text }, i) => (
          <li key={title} className="land-step relative" style={{ transitionDelay: `${i * 250}ms` }}>
            <div className="relative mb-4 grid h-10 w-10 place-items-center rounded-lg border border-l-line bg-l-card text-l-link"><Icon size={20} aria-hidden /></div>
            <h3 className="title-3 text-l-text">{title}</h3><p className="body-6 mt-2 text-l-muted">{text}</p>
          </li>))}
      </ol>
      <div className="land-answer mx-auto mt-12 max-w-[680px] rounded-2xl border border-l-line bg-l-card p-6" style={{ transitionDelay: "1100ms" }} aria-label="An example answer">
        <p className="caption-4 text-l-muted">Example</p>
        <p className="body-5 mt-2 text-l-text">How are roles checked before an endpoint changes data?</p>
        <p className="body-6 mt-4 text-l-muted">Every state changing route calls the policy gateway, which checks the role and the repository scope before the handler runs.
          <span className="land-cite caption-3 ml-2 inline-block rounded-md border border-l-line bg-l-sunk px-2 py-1 text-l-link" style={{ transitionDelay: "1500ms" }}>S1</span>
          <span className="land-cite caption-3 ml-1 inline-block rounded-md border border-l-line bg-l-sunk px-2 py-1 text-l-link" style={{ transitionDelay: "1700ms" }}>S2</span></p>
        <p className="caption-5 mt-4 text-l-muted">Illustration of the answer format. Each number opens the file and line.</p>
      </div>
    </div>
  );
}

/** Change risk as seven signals, and a blast radius that ripples outward. Both are illustrations, labelled as such. */
function IntelVisual() {
  const [ref, shown] = useInView<HTMLDivElement>("0px 0px -20% 0px");
  const nodes = [[200, 40], [330, 90], [330, 190], [200, 240], [70, 190], [70, 90]] as const;
  return (
    <div ref={ref} className={cn("land-intel grid gap-6 lg:grid-cols-2 lg:items-stretch", shown && "is-in")}>
      <div className="rounded-2xl border border-l-line bg-l-card p-6">
        <p className="caption-4 text-l-muted">Change risk, seven signals (illustration)</p>
        <ul className="mt-4 space-y-3">
          {SIGNALS.map(([name, v], i) => (
            <li key={name} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-3"><span className="caption-5 text-l-muted">{name}</span>
              <span className="block h-2 overflow-hidden rounded-full bg-l-sunk"><i className="land-bar block h-2 w-full rounded-full bg-brand" style={{ ["--v" as string]: v, transitionDelay: `${i * 90}ms` }} /></span></li>))}
        </ul>
      </div>
      <div className="rounded-2xl border border-l-line bg-l-card p-6">
        <p className="caption-4 text-l-muted">Blast radius of one change (illustration)</p>
        <svg viewBox="0 0 400 280" className="mt-2 h-auto w-full" role="img" aria-label="One changed function in the middle, with the code that depends on it around it and a ring spreading outward">
          {[0, 1, 2].map((i) => <circle key={i} cx={200} cy={140} r={36} fill="none" stroke="#818cf8" strokeWidth={2} className="land-ring" style={{ animationDelay: `${i * 1.2}s` }} />)}
          {nodes.map(([x, y], i) => <line key={`l${i}`} x1={200} y1={140} x2={x} y2={y} stroke="#94a3b8" strokeWidth={1.5} />)}
          {nodes.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={10} fill={i === 2 ? "#fbbf24" : "#6366f1"} className="land-node" style={{ transitionDelay: `${300 + i * 120}ms` }} />)}
          <circle cx={200} cy={140} r={16} fill="#4f46e5" stroke="var(--color-l-card)" strokeWidth={3} />
        </svg>
        <p className="caption-5 text-l-muted">The amber node is a test that covers the change.</p>
      </div>
    </div>
  );
}

/** The tagline: words start muted and take full colour one at a time as they cross a line near the middle of the screen. */
function TaglineReveal() {
  const words = useRef<(HTMLSpanElement | null)[]>([]);
  const [on, setOn] = useState<Set<number>>(() => (typeof IntersectionObserver === "undefined" || reduced() ? new Set(Array.from({ length: 99 }, (_, i) => i)) : new Set()));
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined" || reduced()) return;
    const io = new IntersectionObserver((entries) => {
      const hit = entries.filter((e) => e.isIntersecting).map((e) => Number((e.target as HTMLElement).dataset.i));
      if (!hit.length) return;
      setOn((prev) => { const next = new Set(prev); hit.forEach((i) => next.add(i)); return next; });
      entries.forEach((e) => { if (e.isIntersecting) io.unobserve(e.target); });
    }, { rootMargin: "0px 0px -35% 0px" });
    words.current.forEach((w) => w && io.observe(w));
    return () => io.disconnect();
  }, []);
  let n = 0;
  return (
    <p className="heading-3 mx-auto max-w-[680px] text-l-text">
      {TAGLINE.map((line) => (
        <span key={line} className="block">
          {line.split(" ").map((w) => { const i = n++; return <span key={i} data-i={i} ref={(el) => { words.current[i] = el; }} className={cn("land-word", on.has(i) && "is-on")}>{w}{" "}</span>; })}
        </span>
      ))}
    </p>
  );
}

/** A small graph that flows from one node to its neighbours: the same idea as the graph page, drawn for the sign in screen. */
function GraphArt() {
  const nodes = [[60, 70], [150, 30], [150, 110], [250, 70], [330, 30], [330, 110]] as const;
  const links = [[0, 1], [0, 2], [1, 3], [2, 3], [3, 4], [3, 5]] as const;
  return (
    <svg viewBox="0 0 390 140" className="h-auto w-full max-w-[480px]" role="img" aria-label="A small graph: one node linked to its neighbours, with the links flowing outward">
      {links.map(([a, b], i) => (
        <g key={i}>
          <line x1={nodes[a][0]} y1={nodes[a][1]} x2={nodes[b][0]} y2={nodes[b][1]} stroke="#94a3b8" strokeWidth={1.5} />
          <line x1={nodes[a][0]} y1={nodes[a][1]} x2={nodes[b][0]} y2={nodes[b][1]} stroke="#818cf8" strokeWidth={2} strokeDasharray="6 8" strokeLinecap="round" className="rl-flow" />
        </g>))}
      {nodes.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={i === 0 ? 11 : 8} fill={i === 3 ? "#fbbf24" : "#6366f1"} stroke="var(--color-l-bg)" strokeWidth={2} />)}
    </svg>
  );
}

function SectionHead({ id, title, children }: { id?: string; title: string; children?: ReactNode }) {
  return (
    <div className="mx-auto mb-12 max-w-[680px] text-center">
      <h2 id={id} className="heading-4 md:heading-3 text-l-text">{title}</h2>
      {children && <p className="body-3 mt-4 text-l-muted">{children}</p>}
    </div>
  );
}

const cardCls = "rounded-2xl border border-l-line bg-l-card p-6";
const iconTile = "mb-4 grid h-10 w-10 place-items-center rounded-lg border border-l-line bg-l-sunk text-l-link";

/** The sign in screen as a landing page. `card` is the sign in form, shown beside the headline so the one action is on the first screen. */
export default function Landing({ card }: { card: ReactNode }) {
  return (
    <div className="landing min-h-screen" id="top">
      <a href="#main" className="body-5 sr-only rounded-lg bg-brand px-3 py-2 text-white focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-40">Skip to content</a>
      <Nav links={NAV} />
      <main id="main" aria-label="About RepoLens AI">
        <section aria-label="Overview" className="mx-auto grid max-w-[1200px] gap-x-12 gap-y-8 px-4 pb-24 pt-24 lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)] lg:items-center lg:pt-32">
          <div className="max-w-[680px]">
            <span className="caption-4 mb-4 inline-flex items-center gap-2 rounded-lg border border-l-line bg-l-sunk px-3 py-1 text-l-muted">Open source, Apache 2.0 <span className="text-l-link">{APP_VERSION}</span></span>
            <h1 className="land-hero-title heading-3 md:heading-1">A living wiki for your code and docs.</h1>
            <p className="body-3 mt-6 max-w-[680px] text-l-muted md:title-2 md:font-normal">Ask questions, see what is out of date, and trace a bug to its cause, with the evidence in front of you.</p>
            <p className="caption-2 mt-6 flex items-center gap-2 text-l-muted"><ShieldCheck size={20} className="shrink-0 text-l-link" aria-hidden />Runs on your server and sends no usage data to its authors.</p>
          </div>
          <div id="sign-in" className="scroll-mt-24 lg:col-start-2 lg:row-span-2 lg:row-start-1">{card}</div>
          <div className="lg:col-start-1 lg:row-start-2"><GraphArt /></div>
        </section>

        <section aria-label="The idea" className="border-y border-l-line bg-l-sunk px-4 py-24 text-center">
          <Reveal><TaglineReveal /></Reveal>
        </section>

        <section id="features" aria-label="What you can do" className="mx-auto max-w-[1200px] scroll-mt-16 px-4 py-24">
          <Reveal>
            <SectionHead title="What you can do with it">Four jobs that usually cost an afternoon, each with the evidence next to the answer.</SectionHead>
            <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {CAN_DO.map(({ icon: Icon, title, text }) => (
                <li key={title} className={cardCls}><div className={iconTile}><Icon size={20} aria-hidden /></div><h3 className="title-3 text-l-text">{title}</h3><p className="body-6 mt-2 text-l-muted">{text}</p></li>))}
            </ul>
          </Reveal>
        </section>

        <section id="ask" aria-label="How Ask works" className="scroll-mt-16 border-y border-l-line bg-l-sunk px-4 py-24">
          <Reveal className="mx-auto max-w-[1200px]">
            <SectionHead title="How Ask works">Four steps from a question to an answer you can check.</SectionHead>
            <AskFlow />
          </Reveal>
        </section>

        <section id="intelligence" aria-label="Repo intelligence" className="mx-auto max-w-[1200px] scroll-mt-16 px-4 py-24">
          <Reveal>
            <SectionHead title="Repo intelligence">The graph and the history behind the answers, ready to use on their own.</SectionHead>
            <ul className="mb-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {INTEL.map(({ icon: Icon, title, text }) => (
                <li key={title} className={cardCls}><div className={iconTile}><Icon size={20} aria-hidden /></div><h3 className="title-3 text-l-text">{title}</h3><p className="body-6 mt-2 text-l-muted">{text}</p></li>))}
            </ul>
            <IntelVisual />
          </Reveal>
        </section>

        <section id="benefits" aria-label="How you benefit" className="scroll-mt-16 border-y border-l-line bg-l-sunk px-4 py-24">
          <Reveal className="mx-auto max-w-[1200px]">
            <SectionHead title="How you benefit">What changes for your team once the answers carry their evidence.</SectionHead>
            <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {BENEFITS.map(({ icon: Icon, title, text }) => (
                <li key={title} className={cardCls}><div className={iconTile}><Icon size={20} aria-hidden /></div><h3 className="title-3 text-l-text">{title}</h3><p className="body-6 mt-2 text-l-muted">{text}</p></li>))}
            </ul>
          </Reveal>
        </section>

        <section id="how" aria-label="How it works" className="mx-auto max-w-[1200px] scroll-mt-16 px-4 py-24">
          <Reveal>
            <SectionHead title="How it works">Three steps, and nothing changes in your code until you say so.</SectionHead>
            <ol className="grid gap-6 md:grid-cols-3">
              {STEPS.map(({ icon: Icon, title, text }, i) => (
                <li key={title} className={cardCls}><div className={iconTile}><Icon size={20} aria-hidden /></div><h3 className="title-3 text-l-text"><span className="text-l-muted">{i + 1}. </span>{title}</h3><p className="body-6 mt-2 text-l-muted">{text}</p></li>))}
            </ol>
          </Reveal>
        </section>

        <section id="practices" aria-label="Best practices" className="scroll-mt-16 border-y border-l-line bg-l-sunk px-4 py-24">
          <Reveal className="mx-auto max-w-[1200px]">
            <SectionHead title="Best practices">Habits that make the answers better and the index trustworthy.</SectionHead>
            <ol className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {PRACTICES.map(({ icon: Icon, title, text }, i) => (
                <li key={title} className={cardCls}><div className={iconTile}><Icon size={20} aria-hidden /></div><h3 className="title-3 text-l-text"><span className="text-l-muted">{i + 1}. </span>{title}</h3><p className="body-6 mt-2 text-l-muted">{text}</p></li>))}
            </ol>
          </Reveal>
        </section>

        <section id="trust" aria-label="Why you can trust it" className="mx-auto max-w-[1200px] scroll-mt-16 px-4 py-24">
          <Reveal>
            <SectionHead title="Why you can trust it">Each line below is something the code does and a test or a document backs.</SectionHead>
            <ul className="mx-auto grid max-w-[920px] gap-6 md:grid-cols-2">
              {TRUST.map(({ icon: Icon, title, text }) => (
                <li key={title} className={cn(cardCls, "flex gap-4")}><Icon size={24} className="shrink-0 text-l-link" aria-hidden /><div><h3 className="title-3 text-l-text">{title}</h3><p className="body-6 mt-1 text-l-muted">{text}</p></div></li>))}
              <li className="flex gap-4 rounded-2xl border border-amber-600/40 bg-l-sunk p-6 md:col-span-2"><ClipboardText size={24} className="shrink-0 text-amber-600" aria-hidden />
                <p className="body-6 text-l-text">Honest limits: this is a {APP_VERSION} release and has not had an independent security audit or a compliance certification. Judge it by the code and the tests.</p></li>
            </ul>
            <p className="caption-2 mt-8 flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
              <Link className="inline-flex items-center gap-2 text-l-link hover:underline" to="/security"><ShieldCheck size={16} aria-hidden />How we think about security</Link>
              <Link className="inline-flex items-center gap-2 text-l-link hover:underline" to="/about">About RepoLens AI</Link>
              <Link className="inline-flex items-center gap-2 text-l-link hover:underline" to="/faq">Questions about trust</Link>
              <a className="inline-flex items-center gap-2 text-l-link hover:underline" href={REPO_URL} target="_blank" rel="noopener noreferrer"><GitPullRequest size={16} aria-hidden />Read the source</a>
              <span className="text-l-muted">Official code: github.com/gnsaddy/repolens</span>
            </p>
          </Reveal>
        </section>

        <section id="faq" aria-label="Questions and answers" className="scroll-mt-16 border-y border-l-line bg-l-sunk px-4 py-24">
          <Reveal className="mx-auto max-w-[760px]">
            <SectionHead title="Questions people ask" />
            <div className="space-y-3">
              {FAQ.map(({ q, a }) => (
                <details key={q} className="group rounded-2xl border border-l-line bg-l-card px-6 py-4">
                  <summary className="title-3 flex cursor-pointer list-none items-center justify-between gap-4 text-l-text [&::-webkit-details-marker]:hidden">{q}
                    <CaretDown size={20} className="shrink-0 text-l-muted transition-transform duration-500 ease-land group-open:rotate-180" aria-hidden /></summary>
                  <p className="body-6 mt-3 text-l-muted">{a}</p>
                </details>))}
            </div>
          </Reveal>
        </section>

        <section aria-label="Get started" className="mx-auto max-w-[1200px] px-4 py-24 text-center">
          <Reveal>
            <h2 className="heading-4 md:heading-3 mx-auto max-w-[680px] text-l-text">Sign in and ask your first question.</h2>
            <p className="body-3 mx-auto mt-4 max-w-[680px] text-l-muted">Free and open source. It runs on your own server, so there is no account with us and no credit card.</p>
            <a href="#sign-in" className="button-lg mt-8 inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-brand px-3 py-2 text-white transition-colors duration-300 ease-land hover:bg-brand-600 active:scale-[0.98]">Sign in or create a workspace <ArrowRight size={20} aria-hidden /></a>
          </Reveal>
        </section>
      </main>

      <Footer />
    </div>
  );
}
