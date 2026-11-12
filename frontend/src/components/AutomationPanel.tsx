import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowSquareOut, GitPullRequest, Robot } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Spinner, statusTone } from "@/components/ui";
import { api, type AutomationEvent, type AutomationOverview, type AutomationRule, type AutomationRun } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

type Row = { key: string; name: string; rule: AutomationRule };
type EventKey = "ci_failure" | "pr_review";
const PERSONA_NAMES: Record<string, string> = { developer: "Developer", "security-auditor": "Security auditor" };

function RuleRow({ row, event, saved }: { row: Row; event: EventKey; saved: () => void }) {
  const toast = useToast();
  const [on, setOn] = useState(row.rule.enabled);
  const [limit, setLimit] = useState(String(row.rule.daily_limit));
  const [all, setAll] = useState(row.rule.all_branches);
  useEffect(() => { setOn(row.rule.enabled); setLimit(String(row.rule.daily_limit)); setAll(row.rule.all_branches); }, [row.rule]);
  const n = Number(limit);
  const valid = Number.isInteger(n) && n >= 1 && n <= 50;
  const save = useMutation({
    mutationFn: () => api<AutomationRule>(`/automations/rules/${row.key}`, { method: "PUT", json: { event, enabled: on, daily_limit: n, all_branches: all } }),
    onSuccess: () => { saved(); toast(`${row.name}: ${on ? "on" : "off"}`); },
  });
  const dirty = on !== row.rule.enabled || n !== row.rule.daily_limit || all !== row.rule.all_branches;
  const what = event === "pr_review" ? "Review pull requests of" : "Investigate failures of";
  return (
    <li className="flex flex-wrap items-center gap-x-6 gap-y-2 py-3">
      <label className="flex min-w-48 flex-1 items-center gap-2 title-4"><input type="checkbox" aria-label={`${what} ${row.name}`} checked={on} onChange={(e) => setOn(e.target.checked)} />{row.name}</label>
      <label className="flex items-center gap-2 caption-5 text-muted">At most<input aria-label={`Daily limit for ${row.name} (${event})`} className="h-8 w-16 rounded-lg border border-line bg-white px-2 body-6 text-ink" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value)} />a day</label>
      <label className="flex items-center gap-2 caption-5 text-muted"><input type="checkbox" aria-label={`Every branch of ${row.name} (${event})`} checked={all} onChange={(e) => setAll(e.target.checked)} />Every branch</label>
      <Button size="sm" variant="outline" disabled={!dirty || !valid || save.isPending} onClick={() => save.mutate()}>Save</Button>
      {save.error ? <div className="w-full"><ErrorNote error={save.error} /></div> : null}
    </li>
  );
}

function EventCard({ event, data, title, icon, children, saved }: { event: EventKey; data: AutomationEvent; title: string; icon: typeof Robot; children: React.ReactNode; saved: () => void }) {
  const rows: Row[] = [{ key: "all", name: "Every repository", rule: data.all }, ...data.repositories.map((r) => ({ key: r.id, name: r.name, rule: r }))];
  return (
    <Card aria-label={title}>
      <CardTitle icon={icon} right={<span className="caption-5 text-muted">{data.used_today} in the last day</span>}>{title}</CardTitle>
      <div className="space-y-4">
        <div className="body-6 text-muted">{children}</div>
        <ul className="divide-y divide-line">{rows.map((r) => <RuleRow key={`${event}-${r.key}`} row={r} event={event} saved={saved} />)}</ul>
        {!data.repositories.length && <Empty>No repository was added by a git address yet. The rule for every repository still applies to the ones you add.</Empty>}
      </div>
    </Card>
  );
}

function modelNote(ai: boolean, models: string[]) {
  return ai ? <Badge tone="green" title="The text of this report was written by an AI model">AI written{models.length ? ` · ${models.join(", ")}` : ""}</Badge> : <Badge tone="gray" title="No model wrote text for this: it is built from evidence by code">evidence only</Badge>;
}

function RunItem({ r }: { r: AutomationRun }) {
  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="title-4">{r.title || "CI run"}</span>
        <span className="caption-5 text-muted">{r.event === "pr_review" ? "pull request review" : "CI failure"} · {r.repo} · {timeAgo(r.created_at)}</span>
        {r.status !== "started" && <Badge tone={statusTone(r.status === "skipped" ? "unconfirmed" : r.status)}>{r.status}</Badge>}
        {r.link && <a className="inline-flex items-center gap-1 caption-5 text-brand hover:underline" href={r.link} target="_blank" rel="noopener noreferrer">{r.event === "pr_review" ? "Pull request" : "CI run"}<ArrowSquareOut className="h-3 w-3" aria-hidden /></a>}
      </div>
      <p className="caption-5 text-muted">{r.reason}</p>
      {r.investigations.map((i, n) => (
        <p key={i.id} className="flex flex-wrap items-center gap-2 caption-5"><Link className="text-brand hover:underline" to={`/investigations/${i.id}`}>Investigation {n + 1}</Link><span className="text-muted">{i.status}</span>{modelNote(i.ai, i.models)}</p>))}
      {r.reviews.map((v) => (
        <p key={v.persona} className="flex flex-wrap items-center gap-2 caption-5">
          {v.id ? <Link className="text-brand hover:underline" to={`/personas?run=${v.id}`}>{PERSONA_NAMES[v.persona] ?? v.persona} review</Link> : <span>{PERSONA_NAMES[v.persona] ?? v.persona} review</span>}
          <span className="text-muted">{v.status === "done" ? `${v.findings} finding${v.findings === 1 ? "" : "s"}` : v.status}</span>{v.status === "done" && modelNote(v.ai, v.model ? [v.model] : [])}</p>))}
      {r.files.length > 0 && <p className="caption-5 text-muted">Read: {r.files.join(", ")}</p>}
    </li>
  );
}

/** Admins: let RepoLens work by itself when something happens on GitHub, see which model does it, and see what it did. */
export default function AutomationPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["automations"], queryFn: () => api<AutomationOverview>("/automations"), refetchInterval: 15000 });
  const o = q.data;
  const refresh = () => qc.invalidateQueries({ queryKey: ["automations"] });
  if (q.isLoading) return <Card><Spinner /></Card>;
  if (!o) return <Card><ErrorNote error={q.error} /></Card>;
  if (!o.events) return <Card><p role="alert" className="body-6 text-red-600">The server is older than this page and does not send the new data. Restart the backend (it applies the new migration on start), then reload.</p></Card>;
  return (
    <div className="space-y-4">
      <EventCard event="ci_failure" data={o.events.ci_failure} title="Investigate CI failures" icon={Robot} saved={refresh}>
        <p>When a GitHub Actions run fails, RepoLens reads the log of each failed job and starts an investigation for each failing test or error, the same as if you had pasted the log. Unless "Every branch" is ticked, only failures on the repository's own branch are used.</p>
      </EventCard>
      <EventCard event="pr_review" data={o.events.pr_review} title="Review new pull requests" icon={GitPullRequest} saved={refresh}>
        <p>When a pull request is opened, reopened or taken out of draft, the Developer and the Security auditor each review the files it changes, as the pull request has them. The reviews are kept for you to read on the Agent personas page. Nothing is posted on the pull request. This needs a language model.</p>
      </EventCard>
      <p role="note" className="rounded-lg bg-slate-50 p-3 caption-5 text-muted">Both need a GitHub connection (Integrations), the webhook secret set on the server, and the events ticked in the repository's webhook, which points at <code>/api/webhooks/github</code>: <code>Workflow runs</code> for CI failures and <code>Pull requests</code> for reviews. Only repositories added by their git address can use them. Each repository's own line wins over the line for every repository.</p>
      <Card aria-label="Recent runs">
        <CardTitle>Recent runs</CardTitle>
        {!o.runs.length ? <Empty>Nothing has run yet.</Empty> : <ul className="divide-y divide-line">{o.runs.map((r) => <RunItem key={r.id} r={r} />)}</ul>}
      </Card>
    </div>
  );
}
