import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Check, CheckCircle2, Circle, Info, PiggyBank, Search, ShieldAlert } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useState, type FormEvent } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Spark } from "@/components/charts";
import { Badge, Button, Card, CardTitle, Empty, Input, PageHeader, Stat, statusTone } from "@/components/ui";
import { api, type DashInsights, type Dashboard as D, type LLMSettings, type WikiOverview } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn, fmtTokens, pct, timeAgo } from "@/lib/utils";

export default function Dashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const { data } = useQuery({ queryKey: ["dashboard"], queryFn: () => api<D>("/dashboard"), refetchInterval: 10_000 });

  const more = useQuery({ queryKey: ["dashboard-insights"], queryFn: () => api<DashInsights>("/dashboard/insights"), refetchInterval: 60_000 });
  const wiki = useQuery({ queryKey: ["wiki-overview"], queryFn: () => api<WikiOverview>("/wiki/overview") });
  const llm = useQuery({ queryKey: ["llm-settings"], queryFn: () => api<LLMSettings>("/settings/llm") });
  const steps = [
    { done: (data?.repositories ?? 0) > 0, label: "Add a repository", hint: "Pick a Git URL or local path, and choose which branches to index.", to: "/repositories", cta: "Add repository", required: true },
    { done: (wiki.data?.totals?.pages ?? 0) > 0, label: "Add documentation", hint: "Upload a README or PDF, import a web page or connect Confluence.", to: "/wiki?tab=sources", cta: "Add documents", required: true },
    { done: !!more.data?.first_steps?.asked, label: "Ask your first question", hint: "Ask in plain words and get an answer with citations.", to: "/ask", cta: "Open Ask", required: true },
    { done: !!more.data?.first_steps?.measured, label: "Measure it on your repo", hint: "See how often it finds the files behind your own past bug fixes.", to: "/repositories", cta: "Choose a repository", required: false },
    { done: (data?.investigations ?? 0) > 0, label: "Investigate a bug", hint: "Describe it or paste a stack trace above.", to: "/investigations", cta: "Investigate", required: true },
    { done: !!llm.data && llm.data.provider !== "heuristic", label: "Connect an AI model (optional)", hint: "Answers work offline; a model makes them read better.", to: "/settings", cta: "Open settings", required: false },
  ];
  const finished = steps.filter((s) => s.required).every((s) => s.done);
  const next = steps.find((s) => !s.done);
  const doneCount = steps.filter((s) => s.done).length;
  function go(e: FormEvent) {
    e.preventDefault();
    if (q.trim().length >= 5) nav("/investigations", { state: { query: q } });
  }
  const max = Math.max(1, ...(data?.top_repositories.map((r) => r.count) ?? [1]));
  return (
    <div>
      <PageHeader title={`Welcome back${user?.name ? `, ${user.name}` : ""}`} subtitle="Evidence-backed repository intelligence for faster debugging." />
      <form onSubmit={go} className="mb-6 flex gap-2">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Describe a bug, paste a stack trace or ask about your codebase…" aria-label="Investigate" />
        <Button type="submit"><Search className="h-4 w-4" />Investigate</Button>
      </form>
      {!finished && data && (
        <Card className="mb-6" aria-label="Getting started">
          <CardTitle right={<span className="text-xs text-muted">{doneCount} of {steps.length} done</span>}>Getting started</CardTitle>
          <div role="progressbar" aria-label="Setup progress" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={doneCount} className="mb-4 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-brand transition-all" style={{ width: `${(doneCount / steps.length) * 100}%` }} /></div>
          <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{steps.map((s) => (
            <li key={s.label}><Link to={s.to} aria-current={s === next ? "step" : undefined} className={cn("flex h-full gap-2 rounded-lg border p-3 text-sm hover:border-brand", s.done ? "border-emerald-200 bg-emerald-50/50" : s === next ? "border-brand bg-indigo-50/40" : "border-line")}>
              {s.done ? <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" /> : <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted" />}
              <span><span className="block font-medium">{s.label}</span><span className="text-xs text-muted">{s.hint}</span>{s === next && <span className="mt-1 block text-xs font-medium text-brand">Next: {s.cta} →</span>}</span></Link></li>))}</ol>
        </Card>)}
      {more.data?.attention && <Attention items={more.data.attention} />}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Repositories indexed" value={`${data?.indexed_repositories ?? "—"}`} hint={`${data?.repositories ?? 0} connected`} />
        <Stat label="Investigations completed" value={data?.completed ?? "—"} hint={`${data?.investigations ?? 0} total`} />
        <Stat label="Verified accuracy" value={pct(data?.verified_accuracy)} hint={data?.reviewed ? `${data.reviewed} human-reviewed` : "No reviews yet"} />
        <Stat label="Avg. investigation time" value={data?.avg_duration_s != null ? `${data.avg_duration_s}s` : "—"} />
      </div>
      {more.data?.ai && <Insights d={more.data} />}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardTitle right={<Link to="/investigations" className="text-xs text-brand hover:underline">View all</Link>}>Recent investigations</CardTitle>
          {!data?.recent.length ? <Empty>No investigations yet. Add a repository, then describe a bug above.</Empty> : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-medium">Issue / Query</th><th className="font-medium">Repository</th><th className="font-medium">Status</th><th className="text-right font-medium">Time</th></tr></thead>
              <tbody>{data.recent.map((i) => (
                <tr key={i.id} className="border-t border-line">
                  <td className="max-w-xs truncate py-2"><Link className="text-brand hover:underline" to={`/investigations/${i.id}`}>{i.title}</Link></td>
                  <td className="text-muted">{i.repo}</td>
                  <td><Badge tone={statusTone(i.status)}>{i.status}</Badge></td>
                  <td className="text-right text-muted">{timeAgo(i.created_at)}</td>
                </tr>))}</tbody>
            </table>)}
        </Card>
        <Card>
          <CardTitle>Top investigated repositories</CardTitle>
          {!data?.top_repositories.length ? <Empty>Nothing yet.</Empty> : (
            <ul className="space-y-3">{data.top_repositories.map((r) => (
              <li key={r.name} className="text-sm"><div className="mb-1 flex justify-between"><span>{r.name}</span><span className="text-muted">{r.count}</span></div>
                <div className="h-2 rounded-full bg-slate-100"><div className="h-full rounded-full bg-brand" style={{ width: `${(r.count / max) * 100}%` }} /></div></li>))}</ul>)}
        </Card>
      </div>
      {!!more.data?.activity?.length && (
        <Card className="mt-4" aria-label="Recent activity"><CardTitle>Recent activity</CardTitle>
          <ul className="divide-y divide-line text-sm">{more.data.activity.map((a, i) => (
            <li key={i} className="flex items-baseline gap-2 py-2"><span className="font-medium">{a.who}</span>{" "}<span className="text-muted">{a.what}</span>{a.target && <> <code className="truncate text-xs text-muted">{a.target}</code></>}<span className="ml-auto shrink-0 text-xs text-muted">{timeAgo(a.at)}</span></li>))}</ul>
        </Card>)}
    </div>
  );
}


const SEV = { high: ["text-red-700", ShieldAlert], medium: ["text-amber-700", AlertTriangle], low: ["text-amber-600", AlertTriangle], info: ["text-blue-700", Info] } as const;

/** What needs a person's attention, most serious first, each with the place to fix it. */
function Attention({ items }: { items: DashInsights["attention"] }) {
  if (!items.length) return (
    <Card className="mb-6 p-4" aria-label="Needs attention"><p className="flex items-center gap-2 text-sm text-emerald-700"><CheckCircle2 className="h-4 w-4" />Nothing needs your attention right now.</p></Card>);
  return (
    <Card className="mb-6" aria-label="Needs attention">
      <CardTitle right={<span className="text-xs text-muted">{items.length} thing{items.length === 1 ? "" : "s"}</span>}>Needs attention</CardTitle>
      <ul className="divide-y divide-line">{items.map((a) => {
        const [tone, Icon] = SEV[a.severity];
        return <li key={a.text}><Link to={a.to} className="flex items-start gap-2.5 py-2 text-sm hover:text-brand"><Icon className={cn("mt-0.5 h-4 w-4 shrink-0", tone)} /><span>{a.text}</span></Link></li>;
      })}</ul>
    </Card>
  );
}

/** Seven numbers that say whether the workspace is healthy: work trend, AI savings and the knowledge base. */
function Insights({ d }: { d: DashInsights }) {
  const days = d.investigations_per_day.map((x) => ({ ...x, day: x.date.slice(8) }));
  const busy = days.some((x) => x.completed + x.failed > 0);
  return (
    <div className="mb-6 grid gap-4 lg:grid-cols-3" aria-label="Trends">
      <Card><CardTitle right={<span className="text-xs text-muted">{d.week.started} this week</span>}>Investigations, last 14 days</CardTitle>
        {!busy ? <Empty>No investigations in the last two weeks.</Empty> : (
          <div className="h-36"><ResponsiveContainer width="100%" height="100%"><BarChart data={days}><XAxis dataKey="day" fontSize={10} tickLine={false} axisLine={false} /><Tooltip /><Bar dataKey="completed" stackId="a" fill="#4f46e5" name="finished" radius={[0, 0, 0, 0]} /><Bar dataKey="failed" stackId="a" fill="#f87171" name="failed" radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer></div>)}
        <p className="mt-1 text-xs text-muted">{d.week.confirmed} confirmed by a person this week{d.week.failed ? `; ${d.week.failed} failed` : ""}.</p></Card>
      <Card><CardTitle right={<PiggyBank className="h-4 w-4 text-emerald-600" />}>AI use, last 30 days</CardTitle>
        <div className="flex items-baseline gap-2"><span className="text-2xl font-semibold">{fmtTokens(d.ai.tokens)}</span><span className="text-xs text-muted">tokens used</span></div>
        {d.ai.avoided > 0 && <p className="text-sm text-emerald-700">{fmtTokens(d.ai.avoided)} avoided ({Math.round(d.ai.savings_rate * 100)}% saved)</p>}
        <Spark data={d.ai.daily.map((x) => ({ v: x.tokens }))} format={fmtTokens} />
        <p className="text-xs text-muted">{d.ai.requests} model requests{d.ai.helpful_rate != null ? ` · ${Math.round(d.ai.helpful_rate * 100)}% of ${d.ai.ratings} rated answers were helpful` : ""}. <Link to="/insights" className="text-brand hover:underline">More in Insights</Link></p></Card>
      <Card><CardTitle right={<BookOpen className="h-4 w-4 text-brand" />}>Knowledge base</CardTitle>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <div><dt className="text-xs text-muted">Wiki pages</dt><dd className="font-semibold">{d.knowledge.pages.toLocaleString()}</dd></div>
          <div><dt className="text-xs text-muted">Searchable chunks</dt><dd className="font-semibold">{d.knowledge.chunks.toLocaleString()}</dd></div>
          <div><dt className="text-xs text-muted">Graph</dt><dd className="font-semibold">{d.knowledge.graph_nodes.toLocaleString()} nodes</dd></div>
          <div><dt className="text-xs text-muted">Code explained by docs</dt><dd className="font-semibold">{d.knowledge.documented_ratio != null ? `${Math.round(d.knowledge.documented_ratio * 100)}%` : "—"}</dd></div>
        </dl>
        <p className="mt-2 text-xs text-muted">{d.knowledge.wiki_issues ? <><Link to="/wiki?tab=health" className="text-brand hover:underline">{d.knowledge.wiki_issues} wiki issue{d.knowledge.wiki_issues === 1 ? "" : "s"}</Link> to fix</> : "No wiki issues found"}{d.knowledge.drift_open ? ` · ${d.knowledge.drift_open} pages may be out of date` : ""}.</p></Card>
    </div>
  );
}
