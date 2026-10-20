import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDot, FileText, FlaskConical, GitCommit, GitPullRequest, Network, ThumbsDown, ThumbsUp, Ticket } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, CardTitle, CodeBlock, ConfidenceBar, Empty, ErrorNote, Spinner, statusTone } from "@/components/ui";
import { api, type EvidenceItem, type Investigation } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { cn, timeAgo } from "@/lib/utils";

const TABS = ["Overview", "Timeline", "Findings", "Evidence", "Report"] as const;
type Tab = (typeof TABS)[number];

export default function InvestigationView() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("Overview");
  const { data: inv, error } = useQuery({
    queryKey: ["investigation", id],
    queryFn: () => api<Investigation>(`/investigations/${id}`),
    refetchInterval: (q) => (q.state.data && ["completed", "failed"].includes(q.state.data.status) ? false : 1500),
  });
  const review = useMutation({
    mutationFn: (outcome: "confirmed" | "rejected") => api<Investigation>(`/investigations/${id}/review`, { method: "POST", json: { outcome } }),
    onSuccess: (d) => qc.setQueryData(["investigation", id], d),
  });
  const publish = useMutation({
    mutationFn: () => api(`/jira/publish`, { method: "POST", json: { investigation_id: id, approved: true } }),
  });

  if (error) return <ErrorNote error={error} />;
  if (!inv) return <Spinner />;
  const top = inv.hypotheses[0];
  const running = inv.status === "queued" || inv.status === "running";

  return (
    <div>
      <div className="mb-1 text-xs text-muted"><Link to="/investigations" className="hover:underline">Investigations</Link> / {inv.id.slice(0, 8)}</div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{inv.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge tone={statusTone(inv.status)}>{inv.status}</Badge>
            {inv.jira_issue_key && <Badge tone="brand"><Ticket className="mr-1 h-3 w-3" />{inv.jira_issue_key}</Badge>}
            <span>Started {timeAgo(inv.created_at)}</span>
            {inv.evidence.duration_s != null && <span>· {inv.evidence.duration_s}s</span>}
            {inv.cache_hit && <Badge tone="green">cached synthesis</Badge>}
            {running && <Spinner />}
          </div>
        </div>
        {canWrite(user) && inv.status === "completed" && (
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={!inv.jira_issue_key || publish.isPending || publish.isSuccess}
              title={inv.jira_issue_key ? "Posts the RCA as a Jira comment" : "Link a Jira issue first (Jira page)"}
              onClick={() => { if (confirm(`Publish this RCA report to ${inv.jira_issue_key}?`)) publish.mutate(); }}>
              <Ticket className="h-4 w-4" />{publish.isSuccess ? "Published" : "Approve & publish to Jira"}
            </Button>
          </div>)}
      </div>
      <ErrorNote error={publish.error} />
      {inv.status === "failed" && <ErrorNote error={inv.error ?? "Investigation failed"} />}

      <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink")}>{t}</button>))}
      </div>

      {tab === "Overview" && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <div className="mb-3 flex items-center gap-2"><h2 className="text-sm font-semibold">Root cause</h2><Badge tone={statusTone(inv.root_cause_status)}>{inv.root_cause_status}</Badge></div>
            {running ? <p className="text-sm text-muted">Investigating… steps appear in the Timeline tab.</p> : (<>
              <p className="text-sm">{inv.root_cause || "No root cause identified."}</p>
              {top && <div className="mt-4"><div className="mb-1 text-xs text-muted">Confidence (heuristic score, not a probability)</div><ConfidenceBar value={top.confidence} /></div>}
              {top && <p className="mt-4 text-sm text-slate-700">{top.explanation}</p>}
              {inv.suggested_fix && <div className="mt-4 rounded-lg bg-indigo-50 p-3 text-sm"><div className="mb-1 font-medium text-indigo-900">Suggested next steps</div>{inv.suggested_fix}</div>}
            </>)}
            {canWrite(user) && inv.status === "completed" && inv.root_cause_status === "unconfirmed" && (
              <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
                <span className="text-xs text-muted">Was this the real root cause?</span>
                <Button size="sm" variant="outline" onClick={() => review.mutate("confirmed")}><ThumbsUp className="h-3.5 w-3.5" />Confirm</Button>
                <Button size="sm" variant="outline" onClick={() => review.mutate("rejected")}><ThumbsDown className="h-3.5 w-3.5" />Reject</Button>
              </div>)}
          </Card>
          <div className="space-y-4">
            <Card>
              <CardTitle>Verification</CardTitle>
              <div className="flex items-center gap-2 text-sm">
                {inv.verification.valid ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <CircleDot className="h-4 w-4 text-amber-600" />}
                {inv.verification.valid ? `${inv.verification.checked_evidence} citations checked` : "Pending / issues found"}
              </div>
              {inv.verification.problems?.map((p) => <p key={p} className="mt-1 text-xs text-red-600">{p}</p>)}
              <p className="mt-2 text-xs text-muted">{inv.verification.note}</p>
            </Card>
            {!!inv.evidence.memory?.length && (
              <Card><CardTitle>Similar past incidents</CardTitle>
                <ul className="space-y-2 text-sm">{inv.evidence.memory.map((m) => (
                  <li key={m.id}><div className="line-clamp-2">{m.title}</div><div className="text-xs text-muted">{Math.round(m.similarity * 100)}% similar · {m.verified ? "verified" : "unverified"}</div></li>))}</ul></Card>)}
            {!!inv.evidence.callers?.length && (
              <Card><CardTitle>Affected callers <span className="font-normal text-muted">(static analysis)</span></CardTitle>
                <ul className="space-y-1 text-sm">{inv.evidence.callers.slice(0, 6).map((c, i) => (
                  <li key={i} className="flex items-center gap-2"><Network className="h-3.5 w-3.5 text-muted" /><code className="text-xs">{c.caller}</code><span className="truncate text-xs text-muted">{c.file}</span></li>))}</ul></Card>)}
          </div>
        </div>)}

      {tab === "Timeline" && (
        <Card>
          {!inv.timeline.length ? <Empty>Waiting for the first step…</Empty> : (
            <ol className="relative space-y-4 border-l border-line pl-6">
              {inv.timeline.map((s, i) => (
                <li key={i}><span className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full bg-brand" />
                  <div className="flex items-center gap-2 text-sm font-medium capitalize">{s.step}<Badge>{s.agent}</Badge><span className="text-xs font-normal text-muted">{s.duration_ms} ms</span></div>
                  <p className="text-sm text-slate-600">{s.summary}</p></li>))}
            </ol>)}
        </Card>)}

      {tab === "Findings" && (
        <div className="space-y-3">
          {!inv.hypotheses.length ? <Empty>No hypotheses — retrieval found no relevant code.</Empty> : inv.hypotheses.map((h) => (
            <Card key={h.id}>
              <div className="mb-2 flex items-start justify-between gap-3">
                <div className="text-sm font-medium">{h.rank}. {h.title}</div><Badge tone={statusTone(h.status)}>{h.status}</Badge>
              </div>
              <ConfidenceBar value={h.confidence} />
              <p className="mt-2 text-sm text-slate-600">{h.explanation}</p>
            </Card>))}
        </div>)}

      {tab === "Evidence" && (!top ? <Empty>No evidence collected.</Empty> : (
        <div className="space-y-4">{top.evidence.map((e, i) => <EvidenceCard key={i} e={e} />)}</div>))}

      {tab === "Report" && (
        <Card className="prose-report">
          {inv.report_md ? <ReactMarkdown components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">{children}</a> }}>{inv.report_md}</ReactMarkdown> : <Empty>The report is generated when the investigation completes.</Empty>}
        </Card>)}
    </div>
  );
}

/** Link out only to http(s) URLs the server produced; otherwise render the label as plain text. */
function ExtLink({ href, children }: { href?: string; children: React.ReactNode }) {
  return href && /^https?:\/\//.test(href) ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">{children}</a> : <>{children}</>;
}

function EvidenceCard({ e }: { e: EvidenceItem }) {
  if (e.type === "code")
    return (
      <Card>
        <CardTitle right={<Badge>{e.provenance}</Badge>}><ExtLink href={e.url}><code className="text-xs">{e.repo}:{e.file}</code></ExtLink> <span className="font-normal text-muted">lines {e.start_line}–{e.end_line}{e.symbol ? ` · ${e.symbol}` : ""}</span></CardTitle>
        <CodeBlock code={e.snippet ?? ""} startLine={e.start_line} />
      </Card>);
  if (e.type === "commit")
    return (
      <Card>
        <CardTitle right={<Badge>{e.provenance}</Badge>}><span className="flex items-center gap-2"><GitCommit className="h-4 w-4" /><ExtLink href={e.url}><code className="text-xs">{e.sha?.slice(0, 10)}</code></ExtLink> {e.message?.split("\n")[0]}</span></CardTitle>
        <div className="mb-2 text-xs text-muted">{e.author} · {timeAgo(e.committed_at)} · {e.files?.map((f) => `${f.path} (+${f.additions}/-${f.deletions})`).join(", ")}</div>
        {e.diff_excerpt && <pre className="max-h-72 overflow-auto rounded-lg border border-line bg-slate-50 p-3 font-mono text-xs leading-5">{e.diff_excerpt.split("\n").map((l, i) => (
          <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "bg-emerald-100/70" : l.startsWith("-") && !l.startsWith("---") ? "bg-red-100/70" : ""}>{l || " "}</div>))}</pre>}
      </Card>);
  if (e.type === "pr")
    return (
      <Card>
        <CardTitle right={<div className="flex gap-1">{e.jira_keys?.map((k) => <Badge key={k} tone="brand">{k}</Badge>)}<Badge tone={statusTone(e.state === "merged" ? "completed" : "queued")}>{e.state}</Badge></div>}>
          <span className="flex items-center gap-2"><GitPullRequest className="h-4 w-4" />PR #{e.number}: {e.title}</span></CardTitle>
        <div className="mb-2 text-xs text-muted">by {e.author} · <Badge>{e.provenance}</Badge>{e.url && <> · <a className="text-brand hover:underline" href={e.url} target="_blank" rel="noreferrer">open on GitHub</a></>}</div>
        {e.body && <p className="whitespace-pre-wrap text-sm text-slate-700">{e.body.slice(0, 500)}</p>}
      </Card>);
  if (e.type === "doc")
    return (
      <Card>
        <CardTitle right={<Badge tone="blue">{e.relation?.toLowerCase().replace("_", " ")}</Badge>}><span className="flex items-center gap-2"><FileText className="h-4 w-4" />Documentation: {e.title}</span></CardTitle>
        <div className="mb-2 text-xs text-muted"><ExtLink href={e.url}><code>{e.repo}:{e.file}</code></ExtLink> lines {e.start_line}–{e.end_line} · mentions <code>{e.symbol}</code></div>
        <CodeBlock code={e.snippet ?? ""} startLine={e.start_line} />
      </Card>);
  if (e.type === "test")
    return <Card><span className="flex items-center gap-2 text-sm"><FlaskConical className="h-4 w-4" />Covered by test file <code className="text-xs">{e.file}</code> <Badge>{e.provenance}</Badge></span></Card>;
  return <Card><span className="text-sm">Called by <code className="text-xs">{e.caller}</code> in <code className="text-xs">{e.file}</code> <Badge>{e.provenance}</Badge></span></Card>;
}
