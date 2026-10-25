import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardTitle, Empty, PageHeader, Stat } from "@/components/ui";
import { api, type UsageGroup, type UsageSummary } from "@/lib/api";
import { fmtCost, fmtTokens, pct } from "@/lib/utils";

const COLORS = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#64748b"];
const delta = (d: number | null) => (d == null ? undefined : `${d >= 0 ? "↑" : "↓"} ${Math.abs(Math.round(d * 100))}% vs previous period`);

/** A row with no tokens and no cost says nothing, so it is not shown. */
const used = (r: UsageGroup) => r.input_tokens + r.output_tokens > 0 || r.cost > 0;

function Table({ rows: all, label }: { rows: UsageGroup[]; label: string }) {
  const rows = all.filter(used);
  if (!rows.length) return <Empty>No usage recorded yet.</Empty>;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-medium">{label}</th><th className="text-right font-medium">Input</th><th className="text-right font-medium">Output</th><th className="text-right font-medium">Requests</th><th className="text-right font-medium">Latency</th><th className="text-right font-medium">Cost</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.name} className="border-t border-line"><td className="py-2">{r.name}</td>
          <td className="text-right">{fmtTokens(r.input_tokens)}</td><td className="text-right">{fmtTokens(r.output_tokens)}</td>
          <td className="text-right">{r.requests}{r.errors ? <span className="text-red-600"> ({r.errors} err)</span> : null}</td>
          <td className="text-right text-muted">{r.avg_latency_ms} ms</td><td className="text-right">{fmtCost(r.cost)}</td></tr>))}</tbody>
    </table>
  );
}

export default function TokenUsage() {
  const [days, setDays] = useState(30);
  const { data } = useQuery({ queryKey: ["usage", days], queryFn: () => api<UsageSummary>(`/usage/summary?days=${days}`) });
  const t = data?.totals;
  const unpriced = data && t && t.cost === 0 && t.requests > 0;
  return (
    <div>
      <PageHeader title="Token Usage & Cost" subtitle="Measured usage per model, agent and workflow. Local operations are recorded but cost nothing."
        actions={<select aria-label="Period" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={days} onChange={(e) => setDays(+e.target.value)}>
          {[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}</select>} />
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Total tokens" value={t ? fmtTokens(t.input_tokens + t.output_tokens) : "—"} hint={delta(data?.vs_previous.tokens ?? null)} />
        <Stat label="Estimated cost" value={t ? fmtCost(t.cost) : "—"} hint={delta(data?.vs_previous.cost ?? null) ?? `pricing ${data?.pricing_version ?? ""}`} />
        <Stat label="Cache hit rate" value={pct(t?.cache_hit_rate)} hint={t ? `~${fmtTokens(t.tokens_avoided)} tokens avoided (estimate)` : undefined} />
        <Stat label="Cost / successful RCA" value={t?.cost_per_successful_rca != null ? fmtCost(t.cost_per_successful_rca) : "—"} hint={t ? `${t.successful_investigations} completed · ${pct(t.error_rate)} errors` : undefined} />
      </div>
      {unpriced && <div className="mb-4 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">Costs show $0.00 because no price is set for these models. Add each model's price per 1M tokens in <Link className="font-medium underline" to="/settings?tab=ai">Settings → AI &amp; search</Link>, or ask whoever runs the server to set <code>REPOLENS_PRICING_JSON</code>. Prices apply to new usage from then on.</div>}
      <div className="mb-6 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2"><CardTitle>Tokens per day</CardTitle>
          {!data?.daily.length ? <Empty>No data yet.</Empty> : (
            <div className="h-64"><ResponsiveContainer><BarChart data={data.daily}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} tickFormatter={fmtTokens} /><Tooltip formatter={(v) => fmtTokens(Number(v))} /><Bar dataKey="tokens" fill="#4f46e5" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div>)}
        </Card>
        <Card><CardTitle>Share of tokens by model</CardTitle>
          {!data?.by_model.filter(used).length ? <Empty>No data yet.</Empty> : (<>
            <div className="h-40"><ResponsiveContainer><PieChart><Pie data={data.by_model.filter(used).map((m) => ({ name: m.name, value: m.input_tokens + m.output_tokens }))} dataKey="value" innerRadius={40} outerRadius={65}>
              {data.by_model.filter(used).map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}</Pie><Tooltip formatter={(v) => fmtTokens(Number(v))} /></PieChart></ResponsiveContainer></div>
            <ul className="mt-2 space-y-1 text-xs">{data.by_model.filter(used).map((m, i) => <li key={m.name} className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: COLORS[i % COLORS.length] }} /><span className="truncate" title={m.name}>{m.name}</span><span className="ml-auto shrink-0 text-muted">{fmtTokens(m.input_tokens + m.output_tokens)}{m.cost > 0 ? ` · ${fmtCost(m.cost)}` : ""}</span></li>)}</ul></>)}
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-3">
        <Card><CardTitle>By model</CardTitle><Table rows={data?.by_model ?? []} label="Model" /></Card>
        <Card><CardTitle>By agent</CardTitle><Table rows={data?.by_agent ?? []} label="Agent" /></Card>
        <Card><CardTitle>By workflow</CardTitle><Table rows={data?.by_workflow ?? []} label="Workflow" /></Card>
      </div>
      {data && (data.budgets.daily > 0 || data.budgets.monthly > 0) && (
        <p className="mt-4 text-xs text-muted">Budgets: {data.budgets.daily > 0 && `daily ${fmtCost(data.budgets.daily)}`} {data.budgets.monthly > 0 && `monthly ${fmtCost(data.budgets.monthly)}`} — optional inference stops when exceeded.</p>)}
    </div>
  );
}
