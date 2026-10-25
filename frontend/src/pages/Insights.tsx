import { useQuery } from "@tanstack/react-query";
import { Activity, Cpu, Database, Gauge, Network, PiggyBank, Sparkles, Users } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Meter, Spark } from "@/components/charts";
import { Badge, Card, CardTitle, Empty, PageHeader } from "@/components/ui";
import { api, type Calibration, type FeedbackSummary, type MetricsOverview, type SysMetrics } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtBytes, fmtCost, fmtDuration, fmtRate, fmtTokens } from "@/lib/utils";

const n = (v: number) => v.toLocaleString();

function Kpi({ icon, label, value, hint, tone = "text-brand" }: { icon: ReactNode; label: string; value: ReactNode; hint?: ReactNode; tone?: string }) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 text-xs text-muted"><span className={tone}>{icon}</span>{label}</div>
      <div className="mt-1.5 text-2xl font-semibold tracking-tight">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

/** What the workspace did, what AI used and saved, and how busy the server is. */
export default function Insights() {
  const { user } = useAuth();
  const [days, setDays] = useState(30);
  const { data: m } = useQuery({ queryKey: ["metrics", days], queryFn: () => api<MetricsOverview>(`/metrics/overview?days=${days}`) });
  const sys = useQuery({ queryKey: ["system"], enabled: user?.role === "admin", refetchInterval: 5000, retry: false, queryFn: () => api<SysMetrics>("/metrics/system") });
  const fb = useQuery({ queryKey: ["feedback", days], queryFn: () => api<FeedbackSummary>(`/feedback/summary?days=${days}`) });
  const cal = useQuery({ queryKey: ["calibration"], queryFn: () => api<Calibration>("/dashboard/calibration") });
  const models = (m?.ai.by_model ?? []).filter((x) => x.input_tokens + x.output_tokens > 0 || x.cost > 0); // models that did nothing are not worth a row
  const top = Math.max(...models.map((x) => x.input_tokens + x.output_tokens), 1);
  const wouldHave = m ? m.ai.tokens + m.ai.tokens_avoided : 0;

  return (
    <div>
      <PageHeader title="Insights" subtitle="What your team did, what AI used and what it saved, and how busy the server is."
        actions={<select aria-label="Period" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={days} onChange={(e) => setDays(+e.target.value)}>{[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}</select>} />
      {!m ? <p className="text-sm text-muted">Loading…</p> : (<div className="space-y-8">
        <section aria-label="AI utilisation" className="space-y-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Kpi icon={<Sparkles className="h-4 w-4" />} label="Tokens used" value={fmtTokens(m.ai.tokens)} hint={`${n(m.ai.requests)} model requests${m.ai.errors ? ` · ${m.ai.errors} failed` : ""}`} />
            <Kpi icon={<Gauge className="h-4 w-4" />} label="Spend" value={fmtCost(m.ai.cost)} hint={m.ai.cost === 0 && m.ai.tokens > 0 ? "no prices set for these models" : `last ${days} days`} />
            <Kpi icon={<PiggyBank className="h-4 w-4" />} label="Tokens avoided" value={fmtTokens(m.ai.tokens_avoided)} tone="text-emerald-600" hint={`${Math.round(m.ai.savings_rate * 100)}% of what would have been used`} />
            <Kpi icon={<Database className="h-4 w-4" />} label="Answers from memory" value={n(m.memory.hits)} tone="text-violet-600" hint={`${n(m.memory.stored)} remembered`} />
          </div>
          {!!fb.data?.total && (
            <Card className="p-4" aria-label="Answer quality">
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm"><span className="font-medium">{Math.round((fb.data.helpful_rate ?? 0) * 100)}% of rated answers were helpful</span><span className="text-xs text-muted">{fb.data.total} ratings{fb.data.from_memory.total ? ` · answers from memory: ${Math.round((fb.data.from_memory.helpful_rate ?? 0) * 100)}% helpful (${fb.data.from_memory.total})` : ""}</span></div>
              <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-red-100"><div className="bg-emerald-500" style={{ width: `${(fb.data.helpful_rate ?? 0) * 100}%` }} /></div>
            </Card>)}
          {cal.data && cal.data.status !== "empty" && (
            <Card className="p-4" aria-label="Confidence check">
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm"><span className="font-medium">Can the confidence score be trusted?</span><span className="text-xs text-muted">{cal.data.reviewed} reviewed investigations</span></div>
              <p className="mt-1 text-xs text-muted">{cal.data.message}</p>
              <table className="mt-3 w-full text-xs" aria-label="Confidence against outcome">
                <thead><tr className="text-left text-muted"><th className="py-1 font-normal">Score band</th><th className="font-normal">Investigations</th><th className="font-normal">Average score</th><th className="font-normal">Actually confirmed</th></tr></thead>
                <tbody>{cal.data.bands.filter((b) => b.n > 0).map((b) => (
                  <tr key={b.from} className="border-t border-line"><td className="py-1">{Math.round(b.from * 100)}–{Math.round(b.to * 100)}%</td><td>{b.n}</td><td>{Math.round((b.mean_confidence ?? 0) * 100)}%</td><td className="font-medium">{Math.round((b.confirmed_rate ?? 0) * 100)}%</td></tr>))}</tbody>
              </table>
              {cal.data.brier !== undefined && <p className="mt-2 text-xs text-muted">Calibration error {Math.round((cal.data.ece ?? 0) * 100)} points · Brier score {cal.data.brier} · {cal.data.bias}</p>}
            </Card>)}
          {wouldHave > 0 && (
            <Card className="p-4" aria-label="Savings">
              <div className="mb-2 flex items-baseline justify-between text-sm"><span className="font-medium">Without routing, memory and condensing, this would have been {fmtTokens(wouldHave)} tokens</span><span className="text-emerald-700">{Math.round(m.ai.savings_rate * 100)}% saved</span></div>
              <div className="flex h-3 overflow-hidden rounded-full bg-slate-100"><div className="bg-brand" style={{ width: `${(m.ai.tokens / wouldHave) * 100}%` }} title="used" /><div className="bg-emerald-400" style={{ width: `${(m.ai.tokens_avoided / wouldHave) * 100}%` }} title="avoided" /></div>
              <div className="mt-1.5 flex gap-4 text-xs text-muted"><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-brand" />used {fmtTokens(m.ai.tokens)}</span><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-400" />avoided {fmtTokens(m.ai.tokens_avoided)}</span></div>
            </Card>)}
          <div className="grid gap-4 lg:grid-cols-5">
            <Card className="lg:col-span-3"><CardTitle>Tokens per day</CardTitle>
              {!m.ai.daily.length ? <Empty>No model calls in this period.</Empty> : (
                <div className="h-56"><ResponsiveContainer width="100%" height="100%"><AreaChart data={m.ai.daily.map((d) => ({ date: d.date.slice(5), tokens: d.input_tokens + d.output_tokens }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} /><XAxis dataKey="date" fontSize={11} tickLine={false} /><YAxis fontSize={11} tickFormatter={fmtTokens} tickLine={false} axisLine={false} width={44} />
                  <Tooltip formatter={(v) => [fmtTokens(Number(v)), "tokens"]} /><Area type="monotone" dataKey="tokens" stroke="#4f46e5" fill="#c7d2fe" /></AreaChart></ResponsiveContainer></div>)}</Card>
            <Card className="lg:col-span-2"><CardTitle>Which models did the work</CardTitle>
              {!models.length ? <Empty>No model has been used yet.</Empty> : <ul className="space-y-3 text-sm">{models.map((x) => (
                <li key={x.name}><div className="mb-1 flex justify-between gap-2"><span className="truncate font-medium" title={x.name}>{x.name}</span><span className="shrink-0 text-xs text-muted">{fmtTokens(x.input_tokens + x.output_tokens)}{x.cost > 0 ? ` · ${fmtCost(x.cost)}` : ""}</span></div>
                  <div className="h-1.5 overflow-hidden rounded bg-slate-100"><div className="h-full bg-brand" style={{ width: `${Math.max(3, ((x.input_tokens + x.output_tokens) / top) * 100)}%` }} /></div></li>))}</ul>}
              {models.length > 1 && <p className="mt-3 text-xs text-muted">Cheaper models take simple tasks; stronger ones are used when a task is hard.</p>}</Card>
          </div>
        </section>

        {user?.role === "admin" && <ServerLoad sys={sys.data} loading={sys.isLoading} />}

        <section aria-label="Work done" className="space-y-3">
          <h2 className="text-sm font-semibold">Work done</h2>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Kpi icon={<Activity className="h-4 w-4" />} label="Investigations" value={n(m.work.investigations)} hint={`${m.work.completed} completed · ${m.work.confirmed} confirmed`} />
            <Kpi icon={<Gauge className="h-4 w-4" />} label="Average time" value={m.work.avg_minutes != null ? `${m.work.avg_minutes} min` : "—"} hint="to finish an investigation" />
            <Kpi icon={<Sparkles className="h-4 w-4" />} label="Wiki edits" value={n(m.work.proposals_applied)} hint={`${m.work.proposals_pending} waiting for review`} />
            <Kpi icon={<Activity className="h-4 w-4" />} label="Docs possibly out of date" value={n(m.work.drift_open)} tone={m.work.drift_open ? "text-amber-600" : "text-brand"} hint="open drift alerts" />
          </div>
        </section>

        <section aria-label="Knowledge base" className="space-y-3">
          <h2 className="text-sm font-semibold">What is indexed</h2>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Kpi icon={<Database className="h-4 w-4" />} label="Repositories" value={n(m.content.repositories)} hint={m.content.indexing_errors ? <Badge tone="red">{m.content.indexing_errors} with errors</Badge> : "all healthy"} />
            <Kpi icon={<Database className="h-4 w-4" />} label="Wiki pages" value={n(m.content.pages)} hint={`${m.content.doc_collections} collections`} />
            <Kpi icon={<Database className="h-4 w-4" />} label="Searchable chunks" value={n(m.content.chunks)} />
            <Kpi icon={<Network className="h-4 w-4" />} label="Graph" value={`${n(m.content.graph_nodes)} nodes`} hint={`${n(m.content.graph_edges)} connections`} />
          </div>
        </section>

        <section aria-label="Team" className="space-y-3">
          <h2 className="text-sm font-semibold">Team</h2>
          <div className="grid grid-cols-3 gap-4"><Kpi icon={<Users className="h-4 w-4" />} label="People" value={n(m.team.users)} /><Kpi icon={<Users className="h-4 w-4" />} label="Active in period" value={n(m.team.active_users_period)} /><Kpi icon={<Users className="h-4 w-4" />} label="Signed in now" value={n(m.team.active_sessions)} hint="active sessions" /></div>
        </section>
      </div>)}
    </div>
  );
}

/** Live CPU, memory, network and request rate of the server RepoLens runs on. */
function ServerLoad({ sys, loading }: { sys?: SysMetrics; loading: boolean }) {
  if (loading) return null;
  if (!sys?.current) return null;
  const c = sys.current;
  const h = sys.history;
  const memPct = c.mem_used != null && c.mem_total ? (c.mem_used / c.mem_total) * 100 : null;
  const cpu = c.host_cpu;
  return (
    <section aria-label="Server load" className="space-y-3">
      <div className="flex items-center gap-2"><h2 className="text-sm font-semibold">Server load</h2><span className="flex items-center gap-1 text-xs text-muted"><i className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />live · every {sys.interval_s}s · up {fmtDuration(sys.uptime_s)}</span></div>
      {!sys.available ? <Card><p className="text-sm text-muted">This server does not expose CPU and memory readings (they are read from /proc, so Linux and containers are supported).</p></Card> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Card className="p-4"><div className="flex items-center gap-2 text-xs text-muted"><Cpu className="h-4 w-4 text-brand" />CPU</div>
            <div className="mt-1 flex items-baseline gap-2"><span className="text-2xl font-semibold">{cpu != null ? `${Math.round(cpu)}%` : "—"}</span><span className="text-xs text-muted">of {sys.cores} cores · RepoLens {c.process_cpu != null ? `${Math.round(c.process_cpu)}%` : "—"} of one core</span></div>
            <Meter value={cpu} label="CPU used" /><Spark data={h.map((p) => ({ v: p.host_cpu }))} max={100} format={(v) => `${v}%`} />
            {sys.load_average && <div className="text-xs text-muted">load average {sys.load_average.join(" · ")}</div>}</Card>
          <Card className="p-4"><div className="flex items-center gap-2 text-xs text-muted"><Database className="h-4 w-4 text-violet-600" />Memory</div>
            <div className="mt-1 flex items-baseline gap-2"><span className="text-2xl font-semibold">{memPct != null ? `${Math.round(memPct)}%` : "—"}</span><span className="text-xs text-muted">{fmtBytes(c.mem_used)} of {fmtBytes(c.mem_total)} · RepoLens {fmtBytes(c.process_rss)}</span></div>
            <Meter value={memPct} label="Memory used" warn={80} bad={92} /><Spark data={h.map((p) => ({ v: p.mem_used != null && p.mem_total ? (p.mem_used / p.mem_total) * 100 : null }))} max={100} color="#7c3aed" format={(v) => `${Math.round(v)}%`} /></Card>
          <Card className="p-4"><div className="flex items-center gap-2 text-xs text-muted"><Network className="h-4 w-4 text-sky-600" />Network</div>
            <div className="mt-1 text-sm"><span className="text-xl font-semibold">↓ {fmtRate(c.rx_bps)}</span> <span className="ml-2 text-xl font-semibold">↑ {fmtRate(c.tx_bps)}</span></div>
            <div className="text-xs text-muted">all interfaces on the server</div><Spark data={h.map((p) => ({ v: (p.rx_bps ?? 0) + (p.tx_bps ?? 0) }))} color="#0284c7" format={fmtRate} /></Card>
          <Card className="p-4"><div className="flex items-center gap-2 text-xs text-muted"><Activity className="h-4 w-4 text-emerald-600" />API traffic</div>
            <div className="mt-1 flex items-baseline gap-2"><span className="text-2xl font-semibold">{c.rps}</span><span className="text-xs text-muted">requests / s · p95 {c.p95_ms != null ? `${c.p95_ms} ms` : "—"}{c.error_rate > 0 ? ` · ${Math.round(c.error_rate * 100)}% errors` : ""}</span></div>
            <Spark data={h.map((p) => ({ v: p.rps }))} color="#059669" format={(v) => `${v}/s`} /><div className="text-xs text-muted">{sys.threads != null ? `${sys.threads} threads` : ""}</div></Card>
        </div>)}
    </section>
  );
}
