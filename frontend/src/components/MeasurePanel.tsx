import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { useState } from "react";
import Modal from "@/components/Modal";
import { Badge, Button, ErrorNote, Spinner } from "@/components/ui";
import { api, type MeasureList, type MeasureRun, type Repo } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

const pc = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** "Measure it on my repo": how often the right files are found for this repository's own past bug fixes. */
export default function MeasurePanel({ repo, onClose }: { repo: Repo; onClose: () => void }) {
  const qc = useQueryClient();
  const [cases, setCases] = useState(10);
  const runs = useQuery({
    queryKey: ["measure", repo.id], queryFn: () => api<MeasureList>(`/repositories/${repo.id}/measure`),
    refetchInterval: (q) => (q.state.data?.items[0]?.status === "running" ? 3000 : false),
  });
  const start = useMutation({
    mutationFn: () => api<MeasureRun>(`/repositories/${repo.id}/measure`, { method: "POST", json: { cases } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["measure", repo.id] }); qc.invalidateQueries({ queryKey: ["dashboard-insights"] }); },
  });
  const latest = runs.data?.items[0];
  const busy = latest?.status === "running" || start.isPending;
  const r = latest?.status === "completed" ? latest.result : null;
  return (
    <Modal wide title={`Measure on ${repo.name}`} icon={<Gauge className="mt-1 h-5 w-5 text-brand" />} onClose={onClose}
      subtitle="Takes this repository's own bug-fix commits, rebuilds the index as it was just before each fix, and checks whether the files the fix changed are found.">
      <div className="space-y-4 text-sm">
        <p className="rounded-lg bg-slate-50 p-3 text-xs text-muted">Runs on this machine. Nothing is downloaded and no language model is called, so no code leaves your server. It can take several minutes on a large repository.</p>
        {runs.isLoading ? <Spinner /> : !runs.data?.can_run ? (
          <p className="text-muted">{repo.source === "docs" ? "Document collections have no bug-fix history to test." : "Index this repository first so its history is available."}</p>
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-muted">Bug fixes to test<select aria-label="Bug fixes to test" value={cases} onChange={(e) => setCases(+e.target.value)} disabled={busy} className="mt-1 block h-9 rounded-md border border-line bg-white px-2 text-sm text-ink">{[5, 10, 15].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
            <Button disabled={busy} onClick={() => start.mutate()}>{busy ? "Measuring…" : latest ? "Measure again" : "Measure now"}</Button>
          </div>)}
        {start.error && <ErrorNote error={start.error} />}
        {latest?.status === "running" && <p role="status" className="text-muted">Running… this page updates when it finishes.</p>}
        {latest?.status === "failed" && <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-700">{latest.error}</p>}
        {r && latest && (<div aria-label="Measurement result">
          <p className="mb-2 text-xs text-muted">{r.cases} bug fix{r.cases === 1 ? "" : "es"} tested · {timeAgo(latest.finished_at ?? latest.started_at)}</p>
          <div className="grid grid-cols-3 gap-3">
            {([["Top 1", r.investigation.top1], ["Top 3", r.investigation.top3], ["Top 5", r.investigation.top5]] as const).map(([k, v]) => (
              <div key={k} className="rounded-lg border border-line p-3 text-center"><div className="text-2xl font-semibold">{pc(v)}</div><div className="text-xs text-muted">{k}</div></div>))}
          </div>
          <p className="mt-2 text-xs text-muted">Share of bug fixes where the right file was among the top 1 / 3 / 5 suggested.{r.top3_ci95 && <> Top 3 is likely between <strong>{pc(r.top3_ci95[0])}</strong> and <strong>{pc(r.top3_ci95[1])}</strong>.</>} Picking at random would reach about {pc(r.random_top3)} in the top 3.</p>
          <details className="mt-3"><summary className="cursor-pointer text-xs font-medium">Per bug fix</summary>
            <ul className="mt-2 space-y-1 text-xs">{r.rows.map((x) => (<li key={x.commit} className="flex items-center gap-2"><code className="text-muted">{x.commit}</code><span className="min-w-0 flex-1 truncate">{x.report}</span><Badge tone={x.investigation_rank ? "green" : "red"}>{x.investigation_rank ? `found #${x.investigation_rank}` : "missed"}</Badge></li>))}</ul></details>
          <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-muted">{r.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>)}
      </div>
    </Modal>
  );
}
