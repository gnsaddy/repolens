import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Lock, Share2, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Label, PageHeader, Spinner, Textarea } from "@/components/ui";
import { api, type Persona, type PersonaRunView, type PersonaRunShort, type Repo } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

const TONE: Record<string, "red" | "amber" | "blue" | "gray"> = { high: "red", medium: "amber", low: "blue", info: "gray" };
const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/** A named specialist (security auditor, test engineer) reads the code that matches your request and reports findings.
 *  A finding is shown only if it quotes code that is really in the file. */
export default function PersonasPage() {
  const { user } = useAuth();
  const write = canWrite(user);
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [personaKey, setPersonaKey] = useState("");
  const [repoId, setRepoId] = useState("");
  const [request, setRequest] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [reusedId, setReusedId] = useState<string | null>(null);
  const personas = useQuery({ queryKey: ["personas"], queryFn: () => api<{ personas: Persona[] }>("/personas") });
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const list = useQuery({ queryKey: ["persona-runs"], queryFn: () => api<{ runs: PersonaRunShort[] }>("/personas/runs") });
  const one = useQuery({ queryKey: ["persona-run", openId], enabled: !!openId, queryFn: () => api<PersonaRunView>(`/personas/runs/${openId}`), retry: false });
  const all = personas.data?.personas ?? [];
  const persona = all.find((p) => p.key === personaKey) ?? all[0];
  const ready = (repos.data ?? []).filter((r) => r.source !== "docs" && r.status === "ready");
  const repo = repoId || ready[0]?.id || "";
  const make = useMutation({
    mutationFn: (fresh: boolean) => api<PersonaRunView>("/personas/runs", { method: "POST", json: { repo_id: repo, persona: persona?.key, request, fresh } }),
    onSuccess: (d) => {
      setOpenId(d.id); setReusedId(d.reused ? d.id : null);
      qc.setQueryData(["persona-run", d.id], d); qc.invalidateQueries({ queryKey: ["persona-runs"] });
      toast(d.reused ? "Same as your earlier review, no tokens used" : "Review ready");
    },
    onError: () => qc.invalidateQueries({ queryKey: ["persona-runs"] }),
  });
  const share = useMutation({
    mutationFn: (v: { id: string; shared: boolean }) => api<PersonaRunView>(`/personas/runs/${v.id}`, { method: "PATCH", json: { shared: v.shared } }),
    onSuccess: (d) => { qc.setQueryData(["persona-run", d.id], d); qc.invalidateQueries({ queryKey: ["persona-runs"] }); toast(d.shared ? "Shared with the workspace" : "Now private to you"); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/personas/runs/${id}`, { method: "DELETE" }),
    onSuccess: () => { setOpenId(null); qc.invalidateQueries({ queryKey: ["persona-runs"] }); toast("Review deleted"); },
  });
  const ask = async (x: { id: string; title: string }) => { if (await confirm({ title: "Delete this review?", message: x.title || "Untitled", confirmLabel: "Delete", danger: true })) remove.mutate(x.id); };
  const d = one.data;
  return (
    <div>
      <PageHeader title="Agent personas" subtitle="A specialist reads the code that matches your request and reports findings. Each finding quotes the code it is about; ones that do not are dropped. Nothing is run." />
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="space-y-4 lg:col-span-2">
          <Card aria-label="New review">
            <CardTitle>Ask a specialist</CardTitle>
            {!write ? <p className="text-sm text-muted">Members can run reviews. You can read reviews others shared with the workspace.</p> : (
              <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); make.mutate(false); }}>
                <fieldset className="space-y-2" aria-label="Persona">
                  {all.map((p) => (
                    <label key={p.key} className={`block cursor-pointer rounded-lg border p-2 text-sm ${persona?.key === p.key ? "border-brand bg-brand/5" : "border-line"}`}>
                      <input type="radio" name="persona" className="mr-2" checked={persona?.key === p.key} onChange={() => setPersonaKey(p.key)} />
                      <span className="font-medium">{p.name}</span><span className="mt-0.5 block text-xs text-muted">{p.summary}</span>
                    </label>))}
                </fieldset>
                <Label>Repository
                  <select aria-label="Repository" value={repo} onChange={(e) => setRepoId(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-line bg-white px-2 text-sm text-ink">
                    {ready.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Label>
                <Label>What should it look at?
                  <Textarea aria-label="What should it look at?" rows={4} maxLength={2000} value={request} onChange={(e) => setRequest(e.target.value)} placeholder={persona?.placeholder} /></Label>
                <p className="text-xs text-muted">Uses the language model once and spends tokens. It sees the code that matches your request, so use the names in the code. It reads the indexed commit and will say which files it saw whole.</p>
                <ErrorNote error={make.error} />
                <Button type="submit" disabled={make.isPending || !repo || !persona || request.trim().length < 5}><Sparkles className="h-4 w-4" />{make.isPending ? "Reviewing…" : `Ask the ${persona?.name.toLowerCase() ?? "specialist"}`}</Button>
              </form>)}
          </Card>
          <Card aria-label="Reviews">
            <CardTitle>Reviews</CardTitle>
            {list.isLoading ? <Spinner /> : !list.data?.runs.length ? <p className="text-sm text-muted">No reviews yet.</p> : (
              <ul className="divide-y divide-line text-sm">{list.data.runs.map((x) => (
                <li key={x.id} className="flex items-start gap-1">
                  <button className={`min-w-0 flex-1 py-2 text-left hover:bg-slate-50 ${openId === x.id ? "font-medium" : ""}`} onClick={() => setOpenId(x.id)}>
                    <div className="truncate">{x.title || "Untitled"}</div>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">{x.persona_name} · {x.repo} · {x.mine ? "you" : x.author_name} · {timeAgo(x.created_at)} · {tokens(x.tokens.input_tokens + x.tokens.output_tokens)} tokens
                      <Badge tone={x.status === "failed" ? "red" : x.high ? "red" : "green"}>{x.status === "failed" ? "failed" : `${x.found} finding${x.found === 1 ? "" : "s"}`}</Badge>
                      <Badge tone={x.shared ? "blue" : "gray"}>{x.shared ? "shared" : "private"}</Badge></div>
                  </button>
                  {(x.mine || user?.role === "admin") && <Button size="sm" variant="ghost" aria-label={`Delete ${x.title || "review"}`} className="mt-1.5" onClick={() => ask(x)}><Trash2 className="h-4 w-4" /></Button>}
                </li>))}</ul>)}
          </Card>
        </div>

        <div className="space-y-4 lg:col-span-3">
          {!openId ? <Empty>Ask a specialist, or open a review.</Empty> : one.isLoading ? <Spinner /> : !d ? <ErrorNote error={one.error} /> : (
            <Review d={d} reused={d.id === reusedId} admin={user?.role === "admin"} write={write} again={make.isPending ? undefined : () => make.mutate(true)}
              onShare={(shared) => share.mutate({ id: d.id, shared })} onDelete={() => ask(d)} />)}
        </div>
      </div>
    </div>
  );
}

function Review({ d, reused, admin, write, again, onShare, onDelete }: { d: PersonaRunView; reused: boolean; admin: boolean; write: boolean; again?: () => void; onShare: (shared: boolean) => void; onDelete: () => void }) {
  const toast = useToast();
  const copy = (text: string) => navigator.clipboard?.writeText(text).then(() => toast("Copied"), () => toast("Could not copy"));
  const t = d.tokens;
  return (
    <>
      <Card aria-label="Review summary">
        <CardTitle right={<span className="flex items-center gap-2">
          {d.mine && <Button size="sm" variant="outline" onClick={() => onShare(!d.shared)}>{d.shared ? <><Lock className="h-4 w-4" />Make private</> : <><Share2 className="h-4 w-4" />Share with workspace</>}</Button>}
          {(d.mine || admin) && <Button size="sm" variant="outline" aria-label="Delete review" onClick={onDelete}><Trash2 className="h-4 w-4" />Delete</Button>}</span>}>{d.persona_name}: {d.title || "Review"}</CardTitle>
        {reused && (
          <p role="status" className="mb-2 rounded-lg bg-blue-50 p-2 text-xs text-blue-800">This is your earlier review for the same request on the same code, so no tokens were used.
            {write && again && <Button size="sm" variant="outline" className="ml-2" onClick={again}>Ask again anyway</Button>}</p>)}
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <Badge tone={d.status === "failed" ? "red" : "green"}>{d.status}</Badge>
          <Badge tone={d.shared ? "blue" : "gray"}>{d.shared ? `shared by ${d.mine ? "you" : d.author_name}` : "private to you"}</Badge>
          <span>{d.repo} · read at {d.base_sha.slice(0, 8)} · {d.model || "no model"}</span>
          <span aria-label="Tokens used">{tokens(t.input_tokens)} in, {tokens(t.output_tokens)} out, {t.calls} call{t.calls === 1 ? "" : "s"}{t.cost ? `, about $${t.cost.toFixed(4)}` : ""}</span>
        </div>
        {d.status === "failed" ? <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{d.error}</p> : <p className="text-sm">{d.summary}</p>}
      </Card>
      {d.status === "done" && (<>
        <Card aria-label="Findings">
          <CardTitle>Findings ({d.findings.length})</CardTitle>
          {!d.findings.length ? <p className="text-sm text-muted">Nothing was found in the code it saw. That does not mean there is nothing to find.</p> : (
            <ul className="space-y-3">{d.findings.map((f, i) => (
              <li key={i} className="rounded-lg border border-line p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2"><Badge tone={TONE[f.severity] ?? "gray"}>{f.severity}</Badge><span className="font-medium">{f.title}</span>
                  <code className="text-xs text-muted">{f.file}:{f.line}</code></div>
                <pre className="mt-2 overflow-auto rounded bg-slate-50 p-2 text-xs">{f.evidence}</pre>
                {f.explanation && <p className="mt-2">{f.explanation}</p>}
                {f.suggestion && <p className="mt-1 text-muted">{f.suggestion}</p>}
                {f.code && (<div className="mt-2">
                  <div className="flex items-center justify-between text-xs text-muted"><span>Suggested code{f.code_ok === false ? " (does not parse, check it)" : ""}</span>
                    <Button size="sm" variant="ghost" aria-label={`Copy code for ${f.title}`} onClick={() => copy(f.code)}><Copy className="h-4 w-4" /></Button></div>
                  <pre className="overflow-auto rounded bg-slate-50 p-2 text-xs">{f.code}</pre></div>)}
              </li>))}</ul>)}
        </Card>
        {d.notes.dependencies && (
          <Card aria-label="Dependencies">
            <CardTitle>What it was told about other repositories</CardTitle>
            {!d.notes.dependencies.facts.length && !d.notes.dependencies.hidden ? <p className="text-sm text-muted">No other repository was found to use, or be used by, the code it read.</p> : (
              <ul className="space-y-1.5 text-sm">{d.notes.dependencies.facts.map((f, i) => (
                <li key={i}><code>{f.path}</code> {f.direction === "used_by" ? "is used by" : "depends on"} <strong>{f.repo}</strong>
                  <span className="block text-xs text-muted"><code>{f.from}</code> → <code>{f.to}</code>{f.evidence && ` — ${f.evidence}`}</span></li>))}</ul>)}
            {d.notes.dependencies.hidden > 0 && <p className="mt-2 text-xs text-muted">{d.notes.dependencies.hidden} more link{d.notes.dependencies.hidden === 1 ? "" : "s"} involve repositories you cannot access; they are not described.</p>}
          </Card>)}
        <Card aria-label="Coverage">
          <CardTitle>What it saw</CardTitle>
          <ul className="space-y-0.5 text-xs">{d.notes.coverage.map((c) => <li key={c.file}><code>{c.file}</code> <span className="text-muted">{c.whole ? "whole file" : "only the parts around the match"}</span></li>)}</ul>
          {d.notes.dropped > 0 && <p className="mt-2 text-xs text-muted">{d.notes.dropped} finding{d.notes.dropped === 1 ? " was" : "s were"} dropped because {d.notes.dropped === 1 ? "it quoted" : "they quoted"} code that is not in the file, or a file that was not shown.</p>}
          <p className="mt-2 text-xs text-muted">{d.notes.limit}</p>
        </Card>
      </>)}
    </>
  );
}
