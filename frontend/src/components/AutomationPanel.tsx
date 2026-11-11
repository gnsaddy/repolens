import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowSquareOut, Robot } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Spinner, statusTone } from "@/components/ui";
import { api, type AutomationOverview, type AutomationRule } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

type Row = { key: string; name: string; rule: AutomationRule; own: boolean };

function RuleRow({ row, saved }: { row: Row; saved: () => void }) {
  const toast = useToast();
  const [on, setOn] = useState(row.rule.enabled);
  const [limit, setLimit] = useState(String(row.rule.daily_limit));
  const [all, setAll] = useState(row.rule.all_branches);
  useEffect(() => { setOn(row.rule.enabled); setLimit(String(row.rule.daily_limit)); setAll(row.rule.all_branches); }, [row.rule]);
  const n = Number(limit);
  const valid = Number.isInteger(n) && n >= 1 && n <= 50;
  const save = useMutation({
    mutationFn: () => api<AutomationRule>(`/automations/rules/${row.key}`, { method: "PUT", json: { enabled: on, daily_limit: n, all_branches: all } }),
    onSuccess: () => { saved(); toast(`${row.name}: ${on ? "on" : "off"}`); },
  });
  const dirty = on !== row.rule.enabled || n !== row.rule.daily_limit || all !== row.rule.all_branches;
  return (
    <li className="flex flex-wrap items-center gap-x-6 gap-y-2 py-3">
      <label className="flex min-w-48 flex-1 items-center gap-2 title-4"><input type="checkbox" aria-label={`Investigate failures of ${row.name}`} checked={on} onChange={(e) => setOn(e.target.checked)} />{row.name}</label>
      <label className="flex items-center gap-2 caption-5 text-muted">At most<input aria-label={`Daily limit for ${row.name}`} className="h-8 w-16 rounded-lg border border-line bg-white px-2 body-6 text-ink" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value)} />a day</label>
      <label className="flex items-center gap-2 caption-5 text-muted"><input type="checkbox" aria-label={`Every branch of ${row.name}`} checked={all} onChange={(e) => setAll(e.target.checked)} />Every branch</label>
      <Button size="sm" variant="outline" disabled={!dirty || !valid || save.isPending} onClick={() => save.mutate()}>Save</Button>
      {save.error ? <div className="w-full"><ErrorNote error={save.error} /></div> : null}
    </li>
  );
}

/** Admins: let RepoLens investigate a failed CI run by itself, and see what it did. Nothing is posted anywhere. */
export default function AutomationPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["automations"], queryFn: () => api<AutomationOverview>("/automations"), refetchInterval: 15000 });
  const o = q.data;
  const refresh = () => qc.invalidateQueries({ queryKey: ["automations"] });
  const rows: Row[] = o ? [{ key: "all", name: "Every repository", rule: o.all, own: true }, ...o.repositories.map((r) => ({ key: r.id, name: r.name, rule: r, own: r.own_rule }))] : [];
  return (
    <div className="space-y-4">
      <Card aria-label="Investigate CI failures">
        <CardTitle icon={Robot} right={o && <span className="caption-5 text-muted">{o.used_today} started in the last day</span>}>Investigate CI failures automatically</CardTitle>
        {q.isLoading ? <Spinner /> : !o ? <ErrorNote error={q.error} /> : (
          <div className="space-y-4">
            <p className="body-6 text-muted">When a GitHub Actions run fails, RepoLens reads the log of each failed job and starts an investigation for each failing test or error, the same as if you had pasted the log. Nothing is posted or changed on GitHub. It runs as <strong>you</strong>, with your access, and every start is in the audit log. It is off until you turn it on.</p>
            <p role="note" className="rounded-lg bg-slate-50 p-3 caption-5 text-muted">It needs a GitHub connection (Integrations), the webhook secret set on the server, and the <code>Workflow runs</code> event ticked in the repository's webhook, which points at <code>/api/webhooks/github</code>. Only repositories added by their git address can use it.</p>
            <ul className="divide-y divide-line">{rows.map((r) => <RuleRow key={r.key} row={r} saved={refresh} />)}</ul>
            {!o.repositories.length && <Empty>No repository was added by a git address yet. The rule for every repository still applies to the ones you add.</Empty>}
            <p className="caption-5 text-muted">A repository's own line wins over the line for every repository. Unless "Every branch" is ticked, only failures on the repository's own branch are used.</p>
          </div>)}
      </Card>
      <Card aria-label="Recent automatic runs">
        <CardTitle>Recent runs</CardTitle>
        {!o?.runs.length ? <Empty>Nothing has run yet.</Empty> : (
          <ul className="divide-y divide-line">
            {o.runs.map((r) => (
              <li key={r.id} className="space-y-1 py-3">
                <div className="flex flex-wrap items-center gap-2"><span className="title-4">{r.title || "CI run"}</span><span className="caption-5 text-muted">{r.repo} · {timeAgo(r.created_at)}</span>
                  {r.status !== "started" && <Badge tone={statusTone(r.status === "skipped" ? "unconfirmed" : r.status)}>{r.status}</Badge>}
                  {r.link && <a className="inline-flex items-center gap-1 caption-5 text-brand hover:underline" href={r.link} target="_blank" rel="noopener noreferrer">CI run<ArrowSquareOut className="h-3 w-3" aria-hidden /></a>}</div>
                <p className="caption-5 text-muted">{r.reason}</p>
                {!!r.investigation_ids.length && <p className="caption-5">{r.investigation_ids.map((id, i) => <Link key={id} className="mr-3 text-brand hover:underline" to={`/investigations/${id}`}>Investigation {i + 1}</Link>)}</p>}
              </li>))}
          </ul>)}
      </Card>
    </div>
  );
}
