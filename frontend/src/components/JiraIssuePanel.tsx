import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Microscope } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Modal from "@/components/Modal";
import { Badge, Button, ErrorNote, Spinner } from "@/components/ui";
import { api, type JiraAnalysis, type JiraClaim, type JiraDetail } from "@/lib/api";
import { cn, timeAgo, safeHref } from "@/lib/utils";

const KIND_TONE: Record<string, "green" | "amber" | "blue" | "brand" | "gray"> = { fix: "green", cause: "brand", workaround: "amber", tried: "gray", repro: "blue", question: "blue", note: "gray" };

function Claims({ title, items }: { title: string; items: JiraClaim[] }) {
  if (!items.length) return null;
  return (
    <section><h4 className="mb-1 text-xs font-medium text-muted">{title}</h4>
      <ul className="space-y-1 text-sm">{items.map((c, i) => <li key={i} className="rounded-md bg-slate-50 px-2.5 py-1.5">{c.text}<span className="ml-2 text-xs text-muted">{c.by || "someone"} · comment {c.comment}</span></li>)}</ul></section>
  );
}

/** One ticket: read it, analyse its description and comments, then investigate with the distilled signals. */
export default function JiraIssuePanel({ issueKey, canInvestigate, onClose }: { issueKey: string; canInvestigate: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const [tab, setTab] = useState<"ticket" | "analysis">("ticket");
  const detail = useQuery({ queryKey: ["jira-issue", issueKey], retry: false, queryFn: () => api<JiraDetail>(`/jira/issues/${issueKey}`) });
  const analysis = useMutation({ mutationFn: () => api<JiraAnalysis>(`/jira/issues/${issueKey}/analyze`, { method: "POST" }), onSuccess: () => setTab("analysis") });
  const investigate = useMutation({ mutationFn: () => api<{ investigation_id: string }>("/jira/investigate", { method: "POST", json: { issue_key: issueKey, repo_ids: [] } }), onSuccess: (r) => nav(`/investigations/${r.investigation_id}`) });
  const d = detail.data;
  const a = analysis.data;
  return (
    <Modal title={d ? `${d.key}: ${d.summary}` : issueKey} subtitle={d ? `${d.status} · ${d.type}${d.priority ? ` · ${d.priority}` : ""}${d.reporter ? ` · reported by ${d.reporter}` : ""}` : undefined} onClose={onClose} wide>
      {detail.isLoading ? <Spinner /> : detail.error ? <ErrorNote error={detail.error} /> : d && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div role="tablist" className="flex gap-1 rounded-lg bg-slate-100 p-1 text-sm">
              {(["ticket", "analysis"] as const).map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => (t === "analysis" && !a ? analysis.mutate() : setTab(t))} className={cn("rounded-md px-3 py-1", tab === t ? "bg-white font-medium shadow-sm" : "text-muted")}>{t === "ticket" ? `Ticket (${d.comments.length} comments)` : "Analysis"}</button>)}
            </div>
            <div className="ml-auto flex gap-2">
              {d.investigation_id ? <Link className="inline-flex h-8 items-center rounded-md border border-line px-3 text-sm text-brand hover:bg-slate-50" to={`/investigations/${d.investigation_id}`}>View RCA<ArrowRight className="ml-1 h-3.5 w-3.5" /></Link>
                : canInvestigate && <Button size="sm" disabled={investigate.isPending} onClick={() => investigate.mutate()}><Microscope className="h-4 w-4" />{investigate.isPending ? "Starting…" : "Investigate"}</Button>}
              <a className="inline-flex h-8 items-center rounded-md border border-line px-3 text-sm hover:bg-slate-50" href={safeHref(d.url)} target="_blank" rel="noopener noreferrer">Open in Jira</a>
            </div>
          </div>
          <ErrorNote error={analysis.error ?? investigate.error} />
          {analysis.isPending && <Spinner />}
          {tab === "ticket" && (
            <div className="space-y-4">
              {!!d.labels.length || !!d.components.length ? <div className="flex flex-wrap gap-1.5">{d.components.map((c) => <Badge key={c} tone="blue">{c}</Badge>)}{d.labels.map((l) => <Badge key={l}>{l}</Badge>)}</div> : null}
              <section><h4 className="mb-1 text-xs font-medium text-muted">Description</h4><pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-sans text-sm">{d.description || "No description."}</pre></section>
              <section><h4 className="mb-1 text-xs font-medium text-muted">Comments</h4>
                {!d.comments.length ? <p className="text-sm text-muted">No comments yet.</p> : <ul className="space-y-2">{d.comments.map((c, i) => <li key={i} className="rounded-lg border border-line p-3 text-sm"><div className="mb-1 text-xs text-muted"><b className="text-ink">{c.author || "Unknown"}</b>{c.created ? ` · ${timeAgo(c.created)}` : ""}</div><p className="whitespace-pre-wrap">{c.body}</p></li>)}</ul>}</section>
            </div>)}
          {tab === "analysis" && a && (
            <div className="space-y-4">
              <p className="rounded-lg bg-indigo-50 p-3 text-sm font-medium text-indigo-900">{a.headline}</p>
              {!!a.flags.length && <ul className="space-y-1">{a.flags.map((f) => <li key={f} className="flex items-start gap-2 text-sm text-amber-800"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{f}</li>)}</ul>}
              <div className="grid gap-3 sm:grid-cols-3 text-sm">
                <div><div className="text-xs text-muted">Age</div><b>{a.age_days ?? "—"} days</b>{a.updated_days != null && <span className="text-xs text-muted"> · updated {a.updated_days}d ago</span>}</div>
                <div><div className="text-xs text-muted">Comments</div><b>{a.discussion.comments}</b></div>
                <div><div className="text-xs text-muted">People</div><b>{a.participants.join(", ") || "—"}</b></div>
              </div>
              {(!!a.signals.errors.length || !!a.signals.files.length || !!a.signals.symbols.length) && (
                <section><h4 className="mb-1 text-xs font-medium text-muted">Signals found in the ticket</h4>
                  <div className="flex flex-wrap gap-1.5">{a.signals.errors.map((e) => <Badge key={e} tone="red">{e}</Badge>)}{a.signals.files.map((f) => <Badge key={f} tone="blue">{f}</Badge>)}{a.signals.symbols.map((s) => <Badge key={s} tone="brand">{s}</Badge>)}</div></section>)}
              <Claims title="Proposed cause" items={a.discussion.cause} />
              <Claims title="Already tried" items={a.discussion.tried} />
              <Claims title="Workarounds" items={a.discussion.workarounds} />
              <Claims title="Fix mentioned" items={a.discussion.fixes} />
              <Claims title="Still unanswered" items={a.discussion.open_questions} />
              {!!a.timeline.length && <section><h4 className="mb-1 text-xs font-medium text-muted">How the discussion went</h4>
                <ol className="space-y-1 text-sm">{a.timeline.map((t, i) => <li key={i} className="flex items-start gap-2"><Badge tone={KIND_TONE[t.kind] ?? "gray"}>{t.kind}</Badge><span><b>{t.author || "Someone"}</b> <span className="text-muted">{t.excerpt}</span></span></li>)}</ol></section>}
              <section><h4 className="mb-1 text-xs font-medium text-muted">Suggested next steps</h4><ol className="list-decimal space-y-1 pl-5 text-sm">{a.next_steps.map((s) => <li key={s}>{s}</li>)}</ol></section>
              {!!a.related.length && <section><h4 className="mb-1 text-xs font-medium text-muted">Related in your docs and code</h4>
                <ul className="space-y-1 text-sm">{a.related.map((r, i) => <li key={i}><Badge tone={r.kind === "doc" ? "green" : "gray"}>{r.kind}</Badge> {r.node_id ? <Link className="text-brand hover:underline" to={`/knowledge?tab=graph&node=${r.node_id}`}>{r.title}</Link> : r.title} <span className="text-xs text-muted">{r.repo}:{r.file}</span></li>)}</ul></section>}
            </div>)}
        </div>)}
    </Modal>
  );
}
