import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import Modal from "@/components/Modal";
import { Badge, Button, Card, CardTitle, ErrorNote, Input, Label, Spinner, Textarea } from "@/components/ui";
import { api, type GoldenOverview, type GoldenResult, type GoldenRunRow } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "—");

/** Questions a workspace expects to be answered well. Re-run them after a change to see what got better or worse. */
export default function QualityPanel({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [mode, setMode] = useState<"retrieval" | "full">("retrieval");
  const data = useQuery({
    queryKey: ["golden"], queryFn: () => api<GoldenOverview>("/golden"),
    refetchInterval: (q) => (q.state.data?.active ? 2500 : false),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["golden"] });
  const run = useMutation({ mutationFn: () => api<GoldenRunRow>("/golden/run", { method: "POST", json: { mode } }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => api(`/golden/questions/${id}`, { method: "DELETE" }), onSuccess: () => { refresh(); toast("Question removed"); } });
  if (data.isLoading) return <Spinner />;
  const d = data.data;
  if (!d) return <ErrorNote error={data.error} />;
  const l = d.latest;
  const busy = !!d.active || run.isPending;
  const failing = (l?.results ?? []).filter((r) => !r.passed);
  return (
    <div className="space-y-4">
      <Card aria-label="Quality checks">
        <CardTitle right={canWrite && <Button size="sm" onClick={() => setAdding(true)}><Plus className="h-4 w-4" />Add a question</Button>}>Quality checks</CardTitle>
        <p className="text-sm text-muted">Save the questions your team expects to be answered well, and what a good answer must be built from. Run them again after changing the model, the skills or the index to see what got better or worse.</p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-xs text-muted">Kind of run<select aria-label="Kind of run" value={mode} onChange={(e) => setMode(e.target.value as "retrieval" | "full")} disabled={busy} className="mt-1 block h-9 rounded-md border border-line bg-white px-2 text-sm text-ink">
            <option value="retrieval">Retrieval only (free)</option><option value="full">Full (the model writes each answer, uses tokens)</option></select></label>
          {canWrite && <Button disabled={busy || !d.questions.length} onClick={() => run.mutate()}>{busy ? "Running…" : "Run the checks"}</Button>}
          {mode === "full" && <span className="text-xs text-muted">Up to {d.max_full} questions; one model call each.</span>}
        </div>
        <ErrorNote error={run.error} />
      </Card>

      {d.changed.length > 0 && <p role="status" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">Since the last run, {d.changed.join(" and ")} changed. Run the checks again to see the effect.</p>}
      {d.active && <p role="status" className="text-sm text-muted">Running: {d.active.done} of {d.active.total} questions…</p>}

      {l && (
        <Card aria-label="Latest result">
          <CardTitle right={<span className="text-xs text-muted">{l.mode === "full" ? "full run" : "retrieval run"}{l.trigger === "reindex" ? ", ran by itself after indexing" : ""} · {timeAgo(l.finished_at ?? l.started_at)}</span>}>Latest result</CardTitle>
          <div className="flex flex-wrap items-baseline gap-3">
            <span className="text-3xl font-semibold">{pct(l.passed, l.total)}</span>
            <span className="text-sm text-muted">{l.passed} of {l.total} questions passed</span>
            {d.compare.delta !== null && <Badge tone={d.compare.delta > 0 ? "green" : d.compare.delta < 0 ? "red" : "gray"}>{d.compare.delta > 0 ? "+" : ""}{Math.round(d.compare.delta * 100)} points since the run before</Badge>}
          </div>
          {d.compare.broke.length > 0 && <div className="mt-3 text-sm"><span className="font-medium text-red-700">Got worse:</span><ul className="list-disc pl-5">{d.compare.broke.map((q) => <li key={q}>{q}</li>)}</ul></div>}
          {d.compare.fixed.length > 0 && <div className="mt-3 text-sm"><span className="font-medium text-emerald-700">Got better:</span><ul className="list-disc pl-5">{d.compare.fixed.map((q) => <li key={q}>{q}</li>)}</ul></div>}
          {failing.length > 0 && <div className="mt-3"><h3 className="mb-1 text-sm font-medium">What failed</h3><ul className="space-y-2">{failing.map((r) => <Failure key={r.id} r={r} />)}</ul></div>}
          {l.mode === "retrieval" && <p className="mt-3 text-xs text-muted">Retrieval runs check which files were found, not how the answer reads. Phrase checks need a full run.</p>}
        </Card>)}

      <Card aria-label="Saved questions">
        <CardTitle>Saved questions ({d.questions.length})</CardTitle>
        {!d.questions.length ? <p className="text-sm text-muted">None yet. Add one here, or choose "Save as a test" under any answer in Ask.</p> : (
          <ul className="divide-y divide-line">{d.questions.map((q) => (
            <li key={q.id} className="flex flex-wrap items-start gap-2 py-2 text-sm">
              {q.last ? (q.last.passed ? <CheckCircle2 aria-label="Passed last run" className="mt-0.5 h-4 w-4 text-emerald-600" /> : <XCircle aria-label="Failed last run" className="mt-0.5 h-4 w-4 text-red-600" />) : <span className="mt-0.5 h-4 w-4" />}
              <div className="min-w-0 flex-1"><div>{q.question}</div>
                <div className="mt-0.5 flex flex-wrap gap-1 text-xs">{q.expect_files.map((f) => <Badge key={f} tone="blue">finds {f}</Badge>)}{q.expect_phrases.map((f) => <Badge key={f}>says “{f}”</Badge>)}{!q.expect_files.length && !q.expect_phrases.length && <span className="text-muted">only checks that something is found</span>}</div></div>
              {canWrite ? <button aria-label={`Remove ${q.question}`} className="text-muted hover:text-red-600" onClick={async () => { if (await confirm({ title: "Remove this question?", message: q.question, confirmLabel: "Remove", danger: true })) remove.mutate(q.id); }}><Trash2 className="h-4 w-4" /></button> : null}
            </li>))}</ul>)}
        <ErrorNote error={remove.error} />
      </Card>
      {adding && <AddQuestion onClose={() => setAdding(false)} onSaved={() => { setAdding(false); refresh(); toast("Question saved"); }} />}
    </div>
  );
}

function Failure({ r }: { r: GoldenResult }) {
  return (
    <li className="rounded-lg border border-line p-2.5 text-sm"><div className="font-medium">{r.question}</div>
      <ul className="mt-1 space-y-0.5 text-xs">{r.checks.filter((c) => !c.ok).map((c) => <li key={c.name} className="text-red-700">✗ {c.name}: {c.detail}</li>)}</ul>
      {r.files.length > 0 && <p className="mt-1 text-xs text-muted">Found instead: {r.files.slice(0, 4).join(", ")}</p>}
    </li>
  );
}

const lines = (t: string) => t.split("\n").map((x) => x.trim()).filter(Boolean);

function AddQuestion({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [q, setQ] = useState("");
  const [files, setFiles] = useState("");
  const [phrases, setPhrases] = useState("");
  const save = useMutation({ mutationFn: () => api("/golden/questions", { method: "POST", json: { question: q.trim(), expect_files: lines(files), expect_phrases: lines(phrases) } }), onSuccess: onSaved });
  return (
    <Modal title="Add a question" subtitle="What should a good answer be built from?" onClose={onClose}>
      <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 5) save.mutate(); }}>
        <label className="block"><Label>Question</Label><Input aria-label="Question" required value={q} onChange={(e) => setQ(e.target.value)} placeholder="How does a user get assigned a role?" /></label>
        <label className="block"><Label>Files a good answer must use (one per line)</Label><Textarea aria-label="Expected files" rows={3} value={files} onChange={(e) => setFiles(e.target.value)} placeholder="api/auth.py" /><span className="text-xs text-muted">A part of the path is enough. Checked on every run.</span></label>
        <label className="block"><Label>Words the answer must contain (one per line)</Label><Textarea aria-label="Expected phrases" rows={2} value={phrases} onChange={(e) => setPhrases(e.target.value)} placeholder="authorize" /><span className="text-xs text-muted">Checked in full runs only.</span></label>
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button disabled={q.trim().length < 5 || save.isPending}>Save question</Button></div>
      </form>
    </Modal>
  );
}
