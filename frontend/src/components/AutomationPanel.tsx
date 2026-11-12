import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowSquareOut, FileMagnifyingGlass, GitPullRequest, Moon, PauseCircle, Robot, Sliders } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Spinner, statusTone } from "@/components/ui";
import { api, type AutomationEvent, type AutomationOverview, type AutomationRule, type AutomationRun } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

type Row = { key: string; name: string; rule: AutomationRule };
type EventKey = "ci_failure" | "pr_review" | "drift_draft" | "watch";
const PERSONA_NAMES: Record<string, string> = { developer: "Developer", "security-auditor": "Security auditor" };
const EVENT_LABEL: Record<EventKey, string> = { ci_failure: "CI failure", pr_review: "pull request review", drift_draft: "stale page", watch: "nightly watch" };
const WHAT: Record<EventKey, string> = { ci_failure: "Investigate failures of", pr_review: "Review pull requests of", drift_draft: "Draft updates for stale pages of", watch: "Watch" };

function RuleRow({ row, event, saved }: { row: Row; event: EventKey; saved: () => void }) {
  const toast = useToast();
  const [on, setOn] = useState(row.rule.enabled);
  const [limit, setLimit] = useState(String(row.rule.daily_limit));
  const [all, setAll] = useState(row.rule.all_branches);
  const [again, setAgain] = useState(!!row.rule.re_review);
  useEffect(() => { setOn(row.rule.enabled); setLimit(String(row.rule.daily_limit)); setAll(row.rule.all_branches); setAgain(!!row.rule.re_review); }, [row.rule]);
  const n = Number(limit);
  const valid = Number.isInteger(n) && n >= 1 && n <= 50;
  const save = useMutation({
    mutationFn: () => api<AutomationRule>(`/automations/rules/${row.key}`, { method: "PUT", json: { event, enabled: on, daily_limit: n, all_branches: all, re_review: again } }),
    onSuccess: () => { saved(); toast(`${row.name}: ${on ? "on" : "off"}`); },
  });
  const dirty = on !== row.rule.enabled || n !== row.rule.daily_limit || all !== row.rule.all_branches || again !== !!row.rule.re_review;
  const branches = event === "ci_failure" || event === "pr_review";
  return (
    <li className="flex flex-wrap items-center gap-x-6 gap-y-2 py-3">
      <label className="flex min-w-48 flex-1 items-center gap-2 title-4"><input type="checkbox" aria-label={`${WHAT[event]} ${row.name}`} checked={on} onChange={(e) => setOn(e.target.checked)} />{row.name}</label>
      {event !== "watch" && <label className="flex items-center gap-2 caption-5 text-muted">At most<input aria-label={`Daily limit for ${row.name} (${event})`} className="h-8 w-16 rounded-lg border border-line bg-white px-2 body-6 text-ink" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value)} />a day</label>}
      {branches && <label className="flex items-center gap-2 caption-5 text-muted"><input type="checkbox" aria-label={`Every branch of ${row.name} (${event})`} checked={all} onChange={(e) => setAll(e.target.checked)} />Every branch</label>}
      {event === "pr_review" && <label className="flex items-center gap-2 caption-5 text-muted" title="Review the pull request again each time a new commit is pushed, at most five times"><input type="checkbox" aria-label={`Review again after new commits of ${row.name}`} checked={again} onChange={(e) => setAgain(e.target.checked)} />Review new commits too</label>}
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

/** Pause the agent, give it a daily token budget, and see what it has used. */
function ControlsCard({ o, saved }: { o: AutomationOverview; saved: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const c = o.controls;
  const [budget, setBudget] = useState(String(c.token_budget));
  useEffect(() => setBudget(String(c.token_budget)), [c.token_budget]);
  const n = Number(budget);
  const valid = Number.isInteger(n) && n >= 0 && n <= 100_000_000;
  const save = useMutation({
    mutationFn: (v: { paused: boolean; token_budget: number }) => api(`/automations/controls`, { method: "PUT", json: v }),
    onSuccess: (_d, v) => { saved(); toast(v.paused !== c.paused ? (v.paused ? "Agent paused" : "Agent resumed") : "Budget saved"); },
  });
  return (
    <Card aria-label="Controls">
      <CardTitle icon={Sliders} right={c.paused ? <Badge tone="amber">paused</Badge> : undefined}>Controls</CardTitle>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <Button size="sm" variant={c.paused ? "primary" : "outline"} disabled={save.isPending}
            onClick={async () => { if (c.paused || await confirm({ title: "Pause the agent?", message: "Nothing starts by itself until you resume it. Runs you start yourself still go ahead, and work that is already going on finishes.", confirmLabel: "Pause" })) save.mutate({ paused: !c.paused, token_budget: c.token_budget }); }}>
            <PauseCircle className="h-4 w-4" aria-hidden />{c.paused ? "Resume the agent" : "Pause the agent"}</Button>
          <label className="flex items-center gap-2 caption-5 text-muted">Daily token budget<input aria-label="Daily token budget" className="h-8 w-28 rounded-lg border border-line bg-white px-2 body-6 text-ink" inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} />tokens (0 means no limit)</label>
          <Button size="sm" variant="outline" disabled={!valid || n === c.token_budget || save.isPending} onClick={() => save.mutate({ paused: c.paused, token_budget: n })}>Save budget</Button>
        </div>
        <p className="caption-5 text-muted">Used in the last day: <strong className="text-ink">{c.tokens_used.toLocaleString()}</strong> tokens{c.token_budget ? ` of ${c.token_budget.toLocaleString()}` : ""}. When the budget is used, nothing starts by itself until the next day. Failed runs in the last day: {c.failed_24h}.</p>
        {c.problems.length > 0 && <ul role="alert" className="space-y-1 rounded-lg bg-amber-50 p-3 caption-5 text-amber-800">{c.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
        {save.error ? <ErrorNote error={save.error} /> : null}
      </div>
    </Card>
  );
}

function modelNote(ai: boolean, models: string[]) {
  return ai ? <Badge tone="green" title="The text of this report was written by an AI model">AI written{models.length ? ` · ${models.join(", ")}` : ""}</Badge> : <Badge tone="gray" title="No model wrote text for this: it is built from evidence by code">evidence only</Badge>;
}

const RETRY_LABEL = { try_again: "Try again", run_anyway: "Run anyway", review_again: "Review again" } as const;

function RunItem({ r }: { r: AutomationRun }) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const refresh = () => { qc.invalidateQueries({ queryKey: ["automations"] }); qc.invalidateQueries({ queryKey: ["agent-inbox"] }); };
  const retry = useMutation({ mutationFn: () => api(`/automations/runs/${r.id}/retry`, { method: "POST" }), onSuccess: () => { refresh(); toast("Started again"); } });
  const cancel = useMutation({ mutationFn: () => api(`/automations/runs/${r.id}/cancel`, { method: "POST" }), onSuccess: () => { refresh(); toast("Cancelled"); } });
  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="title-4">{r.title || "CI run"}</span>
        <span className="caption-5 text-muted">{EVENT_LABEL[r.event]} · {r.repo} · {timeAgo(r.created_at)}{r.tokens > 0 ? ` · ${r.tokens.toLocaleString()} tokens` : ""}</span>
        {r.status !== "started" && <Badge tone={statusTone(r.status === "skipped" || r.status === "cancelled" ? "unconfirmed" : r.status)}>{r.status}</Badge>}
        {r.link && <a className="inline-flex items-center gap-1 caption-5 text-brand hover:underline" href={r.link} target="_blank" rel="noopener noreferrer">{r.event === "pr_review" ? "Pull request" : "CI run"}<ArrowSquareOut className="h-3 w-3" aria-hidden /></a>}
      </div>
      <p className="caption-5 text-muted">{r.reason}</p>
      {r.investigations.map((i, n) => (
        <p key={i.id} className="flex flex-wrap items-center gap-2 caption-5"><Link className="text-brand hover:underline" to={`/investigations/${i.id}`}>Investigation {n + 1}</Link><span className="text-muted">{i.status}</span>{modelNote(i.ai, i.models)}</p>))}
      {r.reviews.map((v) => (
        <p key={v.persona} className="flex flex-wrap items-center gap-2 caption-5">
          {v.id ? <Link className="text-brand hover:underline" to={`/personas?run=${v.id}`}>{PERSONA_NAMES[v.persona] ?? v.persona} review</Link> : <span>{PERSONA_NAMES[v.persona] ?? v.persona} review</span>}
          <span className="text-muted">{v.status === "done" ? `${v.findings} finding${v.findings === 1 ? "" : "s"}${(v.attempts ?? 1) > 1 ? " (second try)" : ""}` : `${v.status}: ${v.summary}`}</span>{v.status === "done" && modelNote(v.ai, v.model ? [v.model] : [])}</p>))}
      {r.files.length > 0 && <p className="caption-5 text-muted">Read: {r.files.join(", ")}</p>}
      {!!r.skipped?.length && <p className="caption-5 text-muted">Not read: {r.skipped.map((s) => `${s.file} (${s.why})`).join(", ")}</p>}
      {r.trace.length > 0 && (
        <details className="caption-5">
          <summary className="cursor-pointer text-muted hover:text-ink">Why it ran, and what it did</summary>
          <ol className="mt-2 list-decimal space-y-1 pl-6 text-muted">{r.trace.map((t, i) => <li key={i}>{t.what}</li>)}</ol>
        </details>)}
      <div className="flex flex-wrap gap-2">
        {r.retry && <Button size="sm" variant="outline" disabled={retry.isPending} onClick={() => retry.mutate()}>{RETRY_LABEL[r.retry]}</Button>}
        {r.can_cancel && <Button size="sm" variant="danger-outline" disabled={cancel.isPending} onClick={async () => { if (await confirm({ title: "Cancel this run?", message: "Its work stops at the next step and what it drafted so far is dismissed. A model call that is already going on is not interrupted.", confirmLabel: "Cancel run" })) cancel.mutate(); }}>Cancel</Button>}
      </div>
      {retry.error ? <ErrorNote error={retry.error} /> : null}
      {cancel.error ? <ErrorNote error={cancel.error} /> : null}
    </li>
  );
}

/** Admins: let RepoLens work by itself when something happens, control it, and see what it did. */
export default function AutomationPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["automations"], queryFn: () => api<AutomationOverview>("/automations"), refetchInterval: 15000 });
  const o = q.data;
  const refresh = () => qc.invalidateQueries({ queryKey: ["automations"] });
  if (q.isLoading) return <Card><Spinner /></Card>;
  if (!o) return <Card><ErrorNote error={q.error} /></Card>;
  if (!o.events || !o.controls) return <Card><p role="alert" className="body-6 text-red-600">The server is older than this page and does not send the new data. Restart the backend (it applies the new migration on start), then reload.</p></Card>;
  return (
    <div className="space-y-4">
      <ControlsCard o={o} saved={refresh} />
      <EventCard event="ci_failure" data={o.events.ci_failure} title="Investigate CI failures" icon={Robot} saved={refresh}>
        <p>When a GitHub Actions run fails, RepoLens reads the log of each failed job and starts an investigation for each failing test or error, the same as if you had pasted the log. If the run belongs to a pull request, a draft comment with the likely cause waits in the Inbox once the investigation has finished. Unless "Every branch" is ticked, only failures on the repository's own branch are used.</p>
      </EventCard>
      <EventCard event="pr_review" data={o.events.pr_review} title="Review new pull requests" icon={GitPullRequest} saved={refresh}>
        <p>When a pull request is opened, reopened or taken out of draft, the Developer and the Security auditor each review the files it changes, as the pull request has them. The reviews are kept on the Agent personas page, and a draft comment waits in the Inbox. Nothing is posted unless you approve it. This needs a language model.</p>
      </EventCard>
      <EventCard event="drift_draft" data={o.events.drift_draft} title="Draft updates for stale pages" icon={FileMagnifyingGlass} saved={refresh}>
        <p>After an index, a wiki page that describes code that has changed gets a drafted update. It waits in the wiki proposals like any other, and nothing is written to the page until you approve it.</p>
      </EventCard>
      <EventCard event="watch" data={o.events.watch} title="Nightly watch" icon={Moon} saved={refresh}>
        <p>Once a day, and at most once a week for each repository, a plain list (no model is used) of pages that have described changed code for more than 14 days is put in the Inbox.</p>
      </EventCard>
      <p role="note" className="rounded-lg bg-slate-50 p-3 caption-5 text-muted">All of them need a GitHub connection (Integrations) and the webhook secret set on the server. CI failures and pull request reviews also need the events ticked in the repository's webhook, which points at <code>/api/webhooks/github</code>: <code>Workflow runs</code> and <code>Pull requests</code>. Only repositories added by their git address can have their own line; each repository's own line wins over the line for every repository.</p>
      <Card aria-label="Recent runs">
        <CardTitle>Recent runs</CardTitle>
        {!o.runs.length ? <Empty>Nothing has run yet.</Empty> : <ul className="divide-y divide-line">{o.runs.map((r) => <RunItem key={r.id} r={r} />)}</ul>}
      </Card>
    </div>
  );
}
