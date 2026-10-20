import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Label, PageHeader, Textarea, statusTone } from "@/components/ui";
import { api, type CiPreview, type InvestigationSummary, type Repo } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { cn, pct, timeAgo } from "@/lib/utils";

export default function Investigations() {
  const { user } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const preset = (useLocation().state as { query?: string } | null)?.query ?? "";
  const [query, setQuery] = useState(preset);
  const [repoIds, setRepoIds] = useState<string[]>([]);
  const [mode, setMode] = useState<"describe" | "ci">("describe");
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const list = useQuery({ queryKey: ["investigations"], queryFn: () => api<InvestigationSummary[]>("/investigations"), refetchInterval: 4000 });
  const start = useMutation({
    mutationFn: () => api<InvestigationSummary>("/investigations", { method: "POST", json: { query, repo_ids: repoIds } }),
    onSuccess: (i) => { qc.invalidateQueries({ queryKey: ["investigations"] }); nav(`/investigations/${i.id}`); },
  });
  const ready = repos.data?.filter((r) => r.status === "ready") ?? [];
  const toggle = (id: string) => setRepoIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <div>
      <PageHeader title="Investigations" subtitle="Root-cause analysis across your repositories, commits and past incidents." />
      {canWrite(user) && (
        <Card className="mb-6">
          <CardTitle>New investigation</CardTitle>
          <div role="tablist" className="mb-4 inline-flex rounded-lg border border-line p-0.5 text-sm">
            {([["describe", "Describe a bug"], ["ci", "From a CI log"]] as const).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={mode === k} onClick={() => setMode(k)} className={cn("rounded-md px-3 py-1", mode === k ? "bg-brand text-white" : "text-muted hover:text-ink")}>{label}</button>))}
          </div>
          {mode === "describe" && (<>
            <Label>Bug report, error message or stack trace</Label>
            <Textarea rows={6} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={"Device onboarding fails after yesterday's deployment…\n\nTraceback (most recent call last): …"} /></>)}
          <div className="mt-3"><Label>Repositories (none selected = search all)</Label>
            <div className="flex flex-wrap gap-2">{ready.map((r) => (
              <button key={r.id} type="button" onClick={() => toggle(r.id)} aria-pressed={repoIds.includes(r.id)}
                className={`rounded-full border px-3 py-1 text-xs ${repoIds.includes(r.id) ? "border-brand bg-indigo-50 text-brand" : "border-line text-muted hover:bg-slate-50"}`}>{r.name}</button>))}
              {!ready.length && <span className="text-xs text-muted">No indexed repositories yet — add one under Repositories.</span>}</div></div>
          {mode === "describe" ? (
            <div className="mt-4 flex items-center gap-3">
              <Button onClick={() => start.mutate()} disabled={query.trim().length < 5 || start.isPending}>Investigate</Button>
              <ErrorNote error={start.error} />
            </div>) : <CiPanel repoIds={repoIds} onStarted={(id) => { qc.invalidateQueries({ queryKey: ["investigations"] }); nav(`/investigations/${id}`); }} />}
        </Card>)}
      <Card>
        <CardTitle>History</CardTitle>
        {!list.data?.length ? <Empty>No investigations yet.</Empty> : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-medium">Title</th><th className="font-medium">Status</th><th className="font-medium">Confidence</th><th className="font-medium">Jira</th><th className="text-right font-medium">Started</th></tr></thead>
            <tbody>{list.data.map((i) => (
              <tr key={i.id} className="border-t border-line">
                <td className="max-w-sm truncate py-2"><Link className="text-brand hover:underline" to={`/investigations/${i.id}`}>{i.title}</Link></td>
                <td><Badge tone={statusTone(i.status)}>{i.status}</Badge></td>
                <td>{i.status === "completed" ? pct(i.confidence) : "—"}</td>
                <td className="text-muted">{i.jira_issue_key ?? "—"}</td>
                <td className="text-right text-muted">{timeAgo(i.created_at)}</td>
              </tr>))}</tbody>
          </table>)}
      </Card>
    </div>
  );
}

const KIND: Record<string, string> = { junit: "JUnit XML", pytest: "pytest", jest: "Jest", go: "Go test", trace: "stack traces" };

/** Paste or upload a CI log / JUnit report, pick the failures worth investigating, and start one investigation each. */
function CiPanel({ repoIds, onStarted }: { repoIds: string[]; onStarted: (firstId: string) => void }) {
  const [log, setLog] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const parse = useMutation({
    mutationFn: () => api<CiPreview>("/investigations/ci/parse", { method: "POST", json: { log } }),
    onSuccess: (r) => setPicked(r.failures.slice(0, 3).map((f) => f.index)),
  });
  const go = useMutation({
    mutationFn: () => api<{ started: { id: string }[] }>("/investigations/ci/start", { method: "POST", json: { log, repo_ids: repoIds, indexes: picked } }),
    onSuccess: (r) => onStarted(r.started[0].id),
  });
  async function onFile(f: File | undefined) {
    if (!f) return;
    if (f.size > 2_000_000) { setLog(""); return; }
    setLog(await f.text());
    parse.reset();
  }
  const r = parse.data;
  return (
    <div>
      <p className="mb-2 text-xs text-muted">Works with JUnit XML, pytest, Jest and Go test output, or any log containing stack traces. Secrets in the log are redacted before anything is stored.</p>
      <label className="mb-2 block text-sm">Upload a report or log <input type="file" accept=".xml,.log,.txt" aria-label="CI log file" className="ml-2 text-xs" onChange={(e) => onFile(e.target.files?.[0])} /></label>
      <Textarea rows={6} value={log} onChange={(e) => { setLog(e.target.value); parse.reset(); }} placeholder="…or paste the failing job's output here" aria-label="CI log" />
      <div className="mt-3 flex items-center gap-3">
        <Button variant="outline" onClick={() => parse.mutate()} disabled={!log.trim() || parse.isPending}>Find failures</Button>
        <ErrorNote error={parse.error || go.error} />
      </div>
      {r && (!r.failures.length ? <p className="mt-3 text-sm text-muted">No failures found in this {KIND[r.format] ?? r.format} log.</p> : (
        <div className="mt-4">
          <div className="mb-2 text-xs text-muted">{r.failures.length} failure(s) found in {KIND[r.format] ?? r.format}. Pick up to 10 to investigate.</div>
          <ul className="divide-y divide-line rounded-lg border border-line">{r.failures.map((f) => (
            <li key={f.index} className="flex items-start gap-3 p-3 text-sm">
              <input type="checkbox" aria-label={`Investigate ${f.name}`} className="mt-1" checked={picked.includes(f.index)} disabled={!picked.includes(f.index) && picked.length >= 10}
                onChange={() => setPicked((p) => (p.includes(f.index) ? p.filter((x) => x !== f.index) : [...p, f.index]))} />
              <div className="min-w-0"><div className="truncate font-medium">{f.name}{f.count > 1 && <Badge className="ml-2">×{f.count}</Badge>}</div>
                <div className="truncate text-xs text-muted">{f.message}</div>{f.file && <div className="text-xs text-muted">{f.file}{f.line ? `:${f.line}` : ""}</div>}</div>
            </li>))}</ul>
          <Button className="mt-3" onClick={() => go.mutate()} disabled={!picked.length || go.isPending}>Investigate {picked.length} failure{picked.length === 1 ? "" : "s"}</Button>
        </div>))}
    </div>
  );
}
