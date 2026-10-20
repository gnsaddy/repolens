import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, FileText, ShieldAlert } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Label, PageHeader, Spinner } from "@/components/ui";
import { api, type Repo, type RiskAssessment, type RiskRow, type RiskScanRow } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { cn, timeAgo } from "@/lib/utils";

const TONE = { low: "green", medium: "amber", high: "red", critical: "red" } as const;
const BAR = { low: "bg-emerald-500", medium: "bg-amber-500", high: "bg-orange-500", critical: "bg-red-600" } as const;
type Mode = "commit" | "range" | "pr" | "files";

export function Gauge({ score, level }: { score: number; level: RiskAssessment["level"] }) {
  return (
    <div className="flex items-center gap-4" role="meter" aria-label="Risk score" aria-valuenow={Math.round(score)} aria-valuemin={0} aria-valuemax={100}>
      <div className="text-4xl font-semibold tabular-nums">{Math.round(score)}</div>
      <div className="flex-1">
        <div className="mb-1 flex items-center gap-2"><Badge tone={TONE[level]}>{level}</Badge><span className="text-xs text-muted">of 100 · a prioritisation score, not a probability of failure</span></div>
        <div className="relative h-2.5 overflow-hidden rounded-full bg-slate-100">
          <div className={cn("h-full rounded-full", BAR[level])} style={{ width: `${Math.min(100, score)}%` }} />
          {[25, 50, 75].map((t) => <span key={t} className="absolute top-0 h-full w-px bg-white" style={{ left: `${t}%` }} />)}
        </div>
      </div>
    </div>
  );
}

export function RiskResult({ r }: { r: RiskAssessment }) {
  const [open, setOpen] = useState<string | null>(null);
  const a = r.affected;
  return (
    <div className="space-y-4">
      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm"><Badge>{r.ref_type}</Badge><span className="font-medium">{r.title || r.ref}</span><code className="text-xs text-muted">{r.ref.slice(0, 24)}</code></div>
        <Gauge score={r.score} level={r.level} />
      </Card>
      <Card>
        <CardTitle>Why this score</CardTitle>
        <ul className="space-y-3">
          {[...r.signals].sort((x, y) => y.points - x.points).map((s) => (
            <li key={s.key}>
              <button className="w-full text-left" onClick={() => setOpen(open === s.key ? null : s.key)} aria-expanded={open === s.key}>
                <div className="flex items-center justify-between text-sm"><span className="font-medium">{s.label}</span><span className="tabular-nums text-muted">{s.points.toFixed(1)} / {(s.weight * 100).toFixed(0)}</span></div>
                <div className="my-1 h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-brand" style={{ width: `${s.value * 100}%` }} /></div>
                <div className="text-xs text-muted">{s.detail}</div>
              </button>
              {open === s.key && !!s.evidence.length && (
                <ul className="mt-2 space-y-1 rounded-md bg-slate-50 p-2 text-xs">{s.evidence.map((e, i) => <li key={i}><code>{Object.entries(e).filter(([k]) => k !== "node_id").map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</code></li>)}</ul>)}
            </li>))}
        </ul>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle>Affected components{!a.symbol_level && <span className="ml-2 text-xs font-normal text-muted">(file level)</span>}</CardTitle>
          {!!a.symbols.length && <div className="mb-3 flex flex-wrap gap-1">{a.symbols.map((s) => <Badge key={s.name + s.path} tone="brand">{s.name}</Badge>)}</div>}
          {!a.dependents.length ? <Empty>No dependents found in the graph.</Empty> : (
            <ul className="space-y-1.5 text-sm">{a.dependents.slice(0, 12).map((d) => (
              <li key={d.node_id + d.rel} title={d.evidence}><code className="text-xs">{d.name}</code> <span className="text-muted">{d.rel === "CALLS" ? "calls" : "imports"} {d.target}</span>
                <div className="text-xs text-muted">{d.repo}:{d.path}{d.cross_repo && <Badge tone="amber" className="ml-2">other repo</Badge>}</div></li>))}</ul>)}
        </Card>
        <div className="space-y-4">
          <Card><CardTitle><span className="flex items-center gap-2"><FlaskConical className="h-4 w-4" />Tests to run</span></CardTitle>
            {!a.tests_to_run.length ? <p className="text-sm text-amber-700">No tests reference this code.</p> : <ul className="space-y-1 text-xs">{a.tests_to_run.map((t) => <li key={t.node_id}><code>{t.path}</code></li>)}</ul>}</Card>
          <Card><CardTitle><span className="flex items-center gap-2"><FileText className="h-4 w-4" />Docs that may need updating</span></CardTitle>
            {!a.docs_to_update.length ? <p className="text-sm text-muted">None found.</p> : <ul className="space-y-1 text-xs">{a.docs_to_update.map((d) => <li key={d.node_id}>{d.name} <span className="text-muted">— {d.path}</span></li>)}</ul>}</Card>
        </div>
      </div>
      <p className="text-xs text-muted">Model {r.model_version}. Known limitations: {r.limitations.join(" ")}</p>
    </div>
  );
}

export default function Risk() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const codeRepos = (repos.data ?? []).filter((r) => r.source !== "docs" && r.status === "ready");
  const [repoId, setRepoId] = useState("");
  useEffect(() => { if (!repoId && codeRepos[0]) setRepoId(codeRepos[0].id); }, [codeRepos, repoId]);
  const [mode, setMode] = useState<Mode>("commit");
  const [f, setF] = useState({ sha: "", base: "main", head: "", pr: "", files: "", title: "" });
  const [result, setResult] = useState<RiskAssessment | null>(null);

  const scan = useQuery({ queryKey: ["risk-scan", repoId], enabled: !!repoId, queryFn: () => api<RiskScanRow[]>(`/risk/scan?repo_id=${repoId}&limit=12`) });
  const history = useQuery({ queryKey: ["risk-history", repoId], enabled: !!repoId, queryFn: () => api<RiskRow[]>(`/risk?repo_id=${repoId}&limit=8`) });
  const run = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<RiskAssessment>("/risk/assess", { method: "POST", json: { repo_id: repoId, ...body } }),
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ["risk-history", repoId] }); },
  });
  const open = useMutation({ mutationFn: (id: string) => api<RiskAssessment>(`/risk/${id}`), onSuccess: setResult });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (mode === "commit") run.mutate({ sha: f.sha.trim() });
    else if (mode === "range") run.mutate({ base: f.base.trim(), head: f.head.trim(), title: f.title });
    else if (mode === "pr") run.mutate({ pr_number: Number(f.pr) });
    else {
      // one "path +add -del" per line, e.g.  src/auth/tokens.py +120 -30
      const files = f.files.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
        const [path, a, d] = l.split(/\s+/);
        return { path, additions: Number((a ?? "").replace("+", "")) || 0, deletions: Number((d ?? "").replace("-", "")) || 0 };
      });
      run.mutate({ files, title: f.title });
    }
  }
  const writable = canWrite(user);
  return (
    <div>
      <PageHeader title="Change Risk" subtitle="Score a commit, branch or PR before it merges — with the signals, the blast radius and the tests to run." />
      <div className="grid gap-4 xl:grid-cols-[340px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardTitle>Assess a change</CardTitle>
            {!codeRepos.length ? <Empty>Index a repository first.</Empty> : (
              <form onSubmit={submit} className="space-y-3">
                <label className="block"><Label>Repository</Label>
                  <select className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={repoId} onChange={(e) => { setRepoId(e.target.value); setResult(null); }}>
                    {codeRepos.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
                <div role="tablist" className="flex gap-1">
                  {([["commit", "Commit"], ["range", "Branch"], ["pr", "PR"], ["files", "Files"]] as const).map(([m, l]) => (
                    <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => setMode(m)} className={cn("rounded-full border px-3 py-1 text-xs", mode === m ? "border-brand bg-indigo-50 text-brand" : "border-line text-muted")}>{l}</button>))}
                </div>
                {mode === "commit" && <label className="block"><Label>Commit SHA</Label><Input required pattern="[0-9a-fA-F]{4,64}" value={f.sha} onChange={(e) => setF({ ...f, sha: e.target.value })} placeholder="ef7278db" /></label>}
                {mode === "range" && (<>
                  <label className="block"><Label>Base</Label><Input required value={f.base} onChange={(e) => setF({ ...f, base: e.target.value })} placeholder="main" /></label>
                  <label className="block"><Label>Head (the proposed branch)</Label><Input required value={f.head} onChange={(e) => setF({ ...f, head: e.target.value })} placeholder="feature/payments" /></label></>)}
                {mode === "pr" && <label className="block"><Label>Pull request number (must be synced)</Label><Input required type="number" min="1" value={f.pr} onChange={(e) => setF({ ...f, pr: e.target.value })} /></label>}
                {mode === "files" && (
                  <label className="block"><Label>Changed files — one per line: path +added -removed</Label>
                    <textarea required rows={4} className="w-full rounded-md border border-line p-2 font-mono text-xs" value={f.files} onChange={(e) => setF({ ...f, files: e.target.value })} placeholder={"src/auth/tokens.py +120 -30\nconfig/settings.yaml +2 -1"} /></label>)}
                <ErrorNote error={run.error} />
                <Button className="w-full" disabled={run.isPending || !writable}>{run.isPending ? "Scoring…" : "Assess risk"}</Button>
                {!writable && <p className="text-xs text-muted">Viewers can read assessments but not create them.</p>}
              </form>)}
          </Card>
          <Card>
            <CardTitle><span className="flex items-center gap-2"><ShieldAlert className="h-4 w-4" />Riskiest recent commits</span></CardTitle>
            {scan.isLoading ? <Spinner /> : !scan.data?.length ? <Empty>No commits indexed.</Empty> : (
              <ul className="space-y-2">{scan.data.map((c) => (
                <li key={c.sha}><button className="flex w-full items-start gap-2 text-left hover:bg-slate-50" onClick={() => { setMode("commit"); setF({ ...f, sha: c.sha }); run.mutate({ sha: c.sha }); }}>
                  <Badge tone={TONE[c.level]} className="mt-0.5 w-10 justify-center tabular-nums">{Math.round(c.score)}</Badge>
                  <span className="min-w-0 flex-1"><span className="block truncate text-sm">{c.title}</span><span className="text-xs text-muted">{c.sha.slice(0, 8)} · {c.top_signals.join(", ") || "no notable signals"}</span></span></button></li>))}</ul>)}
          </Card>
          {!!history.data?.length && (
            <Card><CardTitle>Saved assessments</CardTitle>
              <ul className="space-y-1.5 text-sm">{history.data.map((h) => (
                <li key={h.id}><button className="flex w-full items-center gap-2 text-left hover:underline" onClick={() => open.mutate(h.id)}><Badge tone={TONE[h.level]}>{Math.round(h.score)}</Badge><span className="truncate">{h.title || h.ref}</span><span className="ml-auto text-xs text-muted">{timeAgo(h.created_at)}</span></button></li>))}</ul></Card>)}
        </div>
        <div>{result ? <RiskResult r={result} /> : <Empty>Pick a commit, branch, PR or file list to see its risk breakdown.</Empty>}</div>
      </div>
    </div>
  );
}
