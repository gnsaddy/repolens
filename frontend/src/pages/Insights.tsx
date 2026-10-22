import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, CardTitle, Empty, PageHeader, Stat } from "@/components/ui";
import { api, type MetricsOverview } from "@/lib/api";
import { fmtCost, fmtTokens } from "@/lib/utils";

const n = (v: number) => v.toLocaleString();

function Bar({ value, max, tone = "bg-brand" }: { value: number; max: number; tone?: string }) {
  return <div className="h-1.5 w-full overflow-hidden rounded bg-slate-100"><div className={`h-full ${tone}`} style={{ width: `${max ? Math.max(2, (value / max) * 100) : 0}%` }} /></div>;
}

/** How the workspace is used: what is indexed, what work was done, what AI cost and what memory and routing saved. */
export default function Insights() {
  const [days, setDays] = useState(30);
  const { data: m } = useQuery({ queryKey: ["metrics", days], queryFn: () => api<MetricsOverview>(`/metrics/overview?days=${days}`) });
  const top = Math.max(...(m?.ai.by_model.map((x) => x.input_tokens + x.output_tokens) ?? [0]), 1);
  return (
    <div>
      <PageHeader title="Insights" subtitle="What is indexed, what work was done, what AI used, and what routing and memory saved."
        actions={<select aria-label="Period" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={days} onChange={(e) => setDays(+e.target.value)}>{[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}</select>} />
      {!m ? <p className="text-sm text-muted">Loading…</p> : (<div className="space-y-6">
        <section aria-label="AI utilisation">
          <h2 className="mb-2 text-sm font-semibold">AI utilisation</h2>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Model requests" value={n(m.ai.requests)} hint={m.ai.errors ? `${m.ai.errors} failed` : "none failed"} />
            <Stat label="Tokens used" value={fmtTokens(m.ai.tokens)} hint={fmtCost(m.ai.cost)} />
            <Stat label="Tokens avoided" value={fmtTokens(m.ai.tokens_avoided)} hint={`${Math.round(m.ai.savings_rate * 100)}% of what would have been used`} />
            <Stat label="Answers from memory" value={n(m.memory.hits)} hint={`${n(m.memory.stored)} remembered · ${fmtTokens(m.memory.tokens_saved)} saved`} />
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card><CardTitle>Tokens per day</CardTitle>
              {!m.ai.daily.length ? <Empty>No model calls in this period.</Empty> : (
                <div className="h-52"><ResponsiveContainer width="100%" height="100%"><AreaChart data={m.ai.daily.map((d) => ({ date: d.date.slice(5), tokens: d.input_tokens + d.output_tokens }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="date" fontSize={11} /><YAxis fontSize={11} tickFormatter={fmtTokens} /><Tooltip formatter={(v) => fmtTokens(Number(v))} />
                  <Area type="monotone" dataKey="tokens" stroke="#4f46e5" fill="#c7d2fe" /></AreaChart></ResponsiveContainer></div>)}</Card>
            <Card><CardTitle>Which models did the work</CardTitle>
              {!m.ai.by_model.length ? <Empty>Nothing yet.</Empty> : <ul className="space-y-3 text-sm">{m.ai.by_model.map((x) => (
                <li key={x.name}><div className="mb-1 flex justify-between"><span className="font-medium">{x.name}</span><span className="text-muted">{fmtTokens(x.input_tokens + x.output_tokens)} · {x.requests} calls · {fmtCost(x.cost)}</span></div><Bar value={x.input_tokens + x.output_tokens} max={top} /></li>))}</ul>}
              {m.ai.by_model.length > 1 && <p className="mt-3 text-xs text-muted">Several models share the work: cheaper ones take simple tasks.</p>}</Card>
          </div>
        </section>
        <section aria-label="Work done">
          <h2 className="mb-2 text-sm font-semibold">Work done</h2>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Investigations" value={n(m.work.investigations)} hint={`${m.work.completed} completed · ${m.work.confirmed} confirmed`} />
            <Stat label="Average time" value={m.work.avg_minutes != null ? `${m.work.avg_minutes} min` : "—"} hint="to finish an investigation" />
            <Stat label="Wiki edits" value={n(m.work.proposals_applied)} hint={`${m.work.proposals_pending} waiting for review`} />
            <Stat label="Docs possibly out of date" value={n(m.work.drift_open)} hint="open drift alerts" />
          </div>
        </section>
        <section aria-label="Knowledge base">
          <h2 className="mb-2 text-sm font-semibold">What is indexed</h2>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Repositories" value={n(m.content.repositories)} hint={m.content.indexing_errors ? <Badge tone="red">{m.content.indexing_errors} with errors</Badge> : "all healthy"} />
            <Stat label="Wiki pages" value={n(m.content.pages)} hint={`${m.content.doc_collections} collections`} />
            <Stat label="Searchable chunks" value={n(m.content.chunks)} />
            <Stat label="Graph" value={`${n(m.content.graph_nodes)} nodes`} hint={`${n(m.content.graph_edges)} connections`} />
          </div>
        </section>
        <section aria-label="Team">
          <h2 className="mb-2 text-sm font-semibold">Team</h2>
          <div className="grid grid-cols-3 gap-4"><Stat label="People" value={n(m.team.users)} /><Stat label="Active in period" value={n(m.team.active_users_period)} /><Stat label="Signed in now" value={n(m.team.active_sessions)} hint="active sessions" /></div>
        </section>
      </div>)}
    </div>
  );
}
