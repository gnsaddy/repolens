import { ClipboardCheck, FileClock, GitPullRequest, KeyRound, Lock, Network, ScrollText, Search, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import { Link } from "react-router-dom";
import Logo from "@/components/Logo";
import { ISSUES_URL } from "@/components/ReportIssue";
import { APP_VERSION } from "@/lib/version";

const REPO_URL = ISSUES_URL.replace(/\/issues$/, "");
export const SECURITY_URL = `${REPO_URL}/blob/main/SECURITY.md`;

const CAN_DO = [
  { icon: Sparkles, title: "Ask your docs and code", text: "Answers cite the exact file and line. No model needed for search." },
  { icon: FileClock, title: "Catch stale pages", text: "When code changes, the wiki pages that describe it are flagged for review." },
  { icon: Network, title: "See how it connects", text: "A graph of calls, links and mentions, with facts kept apart from guesses." },
  { icon: Search, title: "Find the root cause", text: "From a bug report or CI log to ranked suspects, with the evidence shown." },
] as const;

// Every line here is something the code does and a test or a document backs. Keep it that way: edit this list only with the code.
const TRUST = [
  { icon: Lock, title: "Your data stays on your server", text: "No AI service is contacted unless you connect one, and Settings shows exactly what leaves." },
  { icon: UserCheck, title: "People decide, not the AI", text: "Fixes, reports and wiki edits are drafts until someone approves them. Nothing is merged for you." },
  { icon: KeyRound, title: "Access you control", text: "Roles, per-repository access, authenticator-app sign-in, single sign-on and an audit log." },
  { icon: ShieldCheck, title: "Secrets are masked", text: "Keys and tokens are stripped before text is indexed or sent to any model you choose." },
  { icon: ScrollText, title: "Open source, no phone-home", text: "Apache 2.0. RepoLens sends no usage data to its authors, so you can read what the code does." },
] as const;

/** A small graph that flows from one node to its neighbours: the same idea as the graph page, drawn for the sign-in screen. */
function Hero() {
  const nodes = [[60, 70], [150, 30], [150, 110], [250, 70], [330, 30], [330, 110]] as const;
  const links = [[0, 1], [0, 2], [1, 3], [2, 3], [3, 4], [3, 5]] as const;
  return (
    <svg viewBox="0 0 390 140" className="h-auto w-full max-w-md" role="img" aria-label="A small graph: one node linked to its neighbours, with the links flowing outward">
      {links.map(([a, b], i) => (
        <g key={i}>
          <line x1={nodes[a][0]} y1={nodes[a][1]} x2={nodes[b][0]} y2={nodes[b][1]} stroke="#475569" strokeWidth={1.5} />
          <line x1={nodes[a][0]} y1={nodes[a][1]} x2={nodes[b][0]} y2={nodes[b][1]} stroke="#818cf8" strokeWidth={2} strokeDasharray="6 8" strokeLinecap="round" className="rl-flow" />
        </g>))}
      {nodes.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={i === 0 ? 11 : 8} fill={i === 3 ? "#fbbf24" : "#6366f1"} stroke="#0f172a" strokeWidth={2} />)}
    </svg>
  );
}

export default function LoginShowcase() {
  return (
    <aside className="hidden max-h-screen flex-col justify-center gap-7 overflow-y-auto p-12 text-slate-300 lg:flex" aria-label="About RepoLens AI">
      <div className="flex items-center gap-2 text-white"><Logo className="h-9 w-9" /><span className="text-xl font-semibold">RepoLens AI</span><span className="rounded bg-white/10 px-1.5 py-0.5 text-xs text-slate-300">{APP_VERSION}</span></div>
      <div>
        <h2 className="max-w-lg text-3xl font-semibold leading-tight text-white">A living wiki for your code and docs.</h2>
        <p className="mt-2 max-w-lg text-sm text-slate-400">Ask questions, see what is out of date, and trace a bug to its cause, with the evidence in front of you.</p>
      </div>
      <Hero />
      <section aria-label="What you can do" className="max-w-lg">
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">What you can do</h3>
        <ul className="grid gap-3 sm:grid-cols-2">
          {CAN_DO.map(({ icon: Icon, title, text }) => (
            <li key={title} className="rounded-lg bg-white/5 p-3"><div className="flex items-center gap-2 text-sm font-medium text-white"><Icon className="h-4 w-4 text-indigo-300" aria-hidden />{title}</div><p className="mt-1 text-xs text-slate-400">{text}</p></li>))}
        </ul>
      </section>
      <section aria-label="Why you can trust it" className="max-w-lg">
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Why you can trust it</h3>
        <ul className="space-y-2.5">
          {TRUST.map(({ icon: Icon, title, text }) => (
            <li key={title} className="flex gap-2.5 text-sm"><Icon className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" aria-hidden /><span><b className="font-medium text-white">{title}.</b> <span className="text-slate-400">{text}</span></span></li>))}
        </ul>
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 p-2.5 text-xs text-amber-100"><ClipboardCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>Honest limits: this is a {APP_VERSION} release and has not had an independent security audit or a compliance certification. Judge it by the code and the tests.</span></p>
        <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <Link className="inline-flex items-center gap-1 text-indigo-300 hover:underline" to="/security"><ShieldCheck className="h-3.5 w-3.5" aria-hidden />How we think about security</Link>
          <Link className="inline-flex items-center gap-1 text-indigo-300 hover:underline" to="/about">About RepoLens AI</Link>
          <Link className="inline-flex items-center gap-1 text-indigo-300 hover:underline" to="/faq">Questions about trust</Link>
          <a className="inline-flex items-center gap-1 text-indigo-300 hover:underline" href={REPO_URL} target="_blank" rel="noopener noreferrer"><GitPullRequest className="h-3.5 w-3.5" aria-hidden />Read the source</a>
          <span className="text-slate-500">Official code: github.com/gnsaddy/repolens</span>
        </p>
      </section>
    </aside>
  );
}

/** The same promise in one line, for screens too narrow to show the panel above. */
export function TrustNote() {
  return (
    <p className="mt-3 flex items-start justify-center gap-1.5 px-2 text-center text-xs text-slate-400 lg:hidden"><Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>Your data stays on your server and nothing is sent to an AI service unless you connect one. <Link className="text-indigo-300 underline" to="/security">How we think about security</Link></span></p>
  );
}
