import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Lock, Share2, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Label, PageHeader, Panel, Spinner, Textarea } from "@/components/ui";
import { api, type Persona, type PersonaDesign, type PersonaOverview, type PersonaRunView, type PersonaRunShort, type Repo } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";
import Markdown from "./wiki/Markdown";

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
  const [mode, setMode] = useState<"review" | "design">("review");
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
  const canDesign = !!persona?.modes?.includes("design") && !!persona.overview;
  const design = canDesign && mode === "design";
  const asked = design && request.trim().length < 5 ? "Describe the design of this repository" : request;  // a blank box means the whole repository
  const make = useMutation({
    mutationFn: (fresh: boolean) => api<PersonaRunView>("/personas/runs", { method: "POST", json: { repo_id: repo, persona: persona?.key, request: asked, fresh, mode: design ? "design" : "review" } }),
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
            {!write ? <p className="body-6 text-muted">Members can run reviews. You can read reviews others shared with the workspace.</p> : (
              <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); make.mutate(false); }}>
                <fieldset className="space-y-2" aria-label="Persona">
                  {all.map((p) => (
                    <label key={p.key} className={`block cursor-pointer rounded-lg border p-2 body-6 ${persona?.key === p.key ? "border-brand bg-brand/5" : "border-line"}`}>
                      <input type="radio" name="persona" className="mr-2" checked={persona?.key === p.key} onChange={() => setPersonaKey(p.key)} />
                      <span className="font-medium">{p.name}</span><span className="mt-0.5 block caption-5 text-muted">{p.summary}</span>
                    </label>))}
                </fieldset>
                {canDesign && (
                  <fieldset className="flex gap-2 body-6" aria-label={`What the ${persona.name.toLowerCase()} does`}>
                    {([["review", "Review (findings)"], ["design", persona.overview?.label ?? "Overview"]] as const).map(([k, label]) => (
                      <label key={k} className={`flex-1 cursor-pointer rounded-lg border p-2 ${mode === k ? "border-brand bg-brand/5" : "border-line"}`}>
                        <input type="radio" name="mode" className="mr-2" checked={mode === k} onChange={() => setMode(k)} />{label}</label>))}
                  </fieldset>)}
                <Label>Repository
                  <select aria-label="Repository" value={repo} onChange={(e) => setRepoId(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-2 body-6 text-ink">
                    {ready.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Label>
                <Label>{design ? "What should it describe? (optional)" : "What should it look at?"}
                  <Textarea aria-label={design ? "What should it describe?" : "What should it look at?"} rows={4} maxLength={2000} value={request} onChange={(e) => setRequest(e.target.value)} placeholder={design ? persona?.overview?.placeholder : persona?.placeholder} /></Label>
                <p className="caption-5 text-muted">{design ? "Uses the language model once and spends tokens (more than a review: it reads the repository's main files). It describes what the code shows and lists what it could not tell." : "Uses the language model once and spends tokens. It sees the code that matches your request, so use the names in the code; if nothing matches it reads the repository's main files. It reads the indexed commit and will say which files it saw whole."}</p>
                <ErrorNote error={make.error} />
                <Button type="submit" disabled={make.isPending || !repo || !persona || (!design && request.trim().length < 5)}><Sparkles className="h-4 w-4" />{make.isPending ? (design ? "Writing…" : "Reviewing…") : design ? (persona?.overview?.action ?? "Write it") : `Ask the ${persona?.name.toLowerCase() ?? "specialist"}`}</Button>
              </form>)}
          </Card>
          <Card aria-label="Reviews">
            <CardTitle>Reviews</CardTitle>
            {list.isLoading ? <Spinner /> : !list.data?.runs.length ? <p className="body-6 text-muted">No reviews yet.</p> : (
              <ul className="divide-y divide-line body-6">{list.data.runs.map((x) => (
                <li key={x.id} className="flex items-start gap-1">
                  <button className={`min-w-0 flex-1 py-2 text-left hover:bg-slate-50 ${openId === x.id ? "font-medium" : ""}`} onClick={() => setOpenId(x.id)}>
                    <div className="truncate">{x.title || "Untitled"}</div>
                    <div className="flex flex-wrap items-center gap-2 caption-5 text-muted">{x.persona_name} · {x.repo} · {x.mine ? "you" : x.author_name} · {timeAgo(x.created_at)} · {tokens(x.tokens.input_tokens + x.tokens.output_tokens)} tokens
                      {x.status === "failed" ? <Badge tone="red">failed</Badge> : <span>{x.mode === "design" ? "overview" : `${x.found} finding${x.found === 1 ? "" : "s"}`}</span>}
                      {x.high > 0 && <Badge tone="red">{x.high} high</Badge>}{x.shared && <span className="inline-flex items-center gap-1"><Share2 className="h-3 w-3" />shared</span>}</div>
                  </button>
                  {(x.mine || user?.role === "admin") && <Button size="sm" variant="ghost" aria-label={`Delete ${x.title || "review"}`} className="mt-2" onClick={() => ask(x)}><Trash2 className="h-4 w-4" /></Button>}
                </li>))}</ul>)}
          </Card>
        </div>

        <div className="space-y-4 lg:col-span-3">
          {!openId ? <Empty>Ask a specialist, or open a review.</Empty> : one.isLoading ? <Spinner /> : !d ? <ErrorNote error={one.error} /> : (
            <Review d={d} titles={all.find((p) => p.key === d.persona)?.overview ?? undefined} reused={d.id === reusedId} admin={user?.role === "admin"} write={write} again={make.isPending ? undefined : () => make.mutate(true)}
              onShare={(shared) => share.mutate({ id: d.id, shared })} onDelete={() => ask(d)} />)}
        </div>
      </div>
    </div>
  );
}

function Review({ d, titles, reused, admin, write, again, onShare, onDelete }: { d: PersonaRunView; titles?: PersonaOverview; reused: boolean; admin: boolean; write: boolean; again?: () => void; onShare: (shared: boolean) => void; onDelete: () => void }) {
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
          <p role="status" className="mb-2 rounded-lg bg-blue-50 p-2 caption-5 text-blue-800">This is your earlier review for the same request on the same code, so no tokens were used.
            {write && again && <Button size="sm" variant="outline" className="ml-2" onClick={again}>Ask again anyway</Button>}</p>)}
        <div className="mb-2 flex flex-wrap items-center gap-2 caption-5 text-muted">
          {d.status === "failed" && <Badge tone="red">failed</Badge>}
          <span className="inline-flex items-center gap-1">{d.shared ? <Share2 className="h-3 w-3" /> : <Lock className="h-3 w-3" />}{d.shared ? `shared by ${d.mine ? "you" : d.author_name}` : "private to you"}</span>
          <span>{d.repo} · read at {d.base_sha.slice(0, 8)} · {d.model || "no model"}</span>
          <span aria-label="Tokens used">{tokens(t.input_tokens)} in, {tokens(t.output_tokens)} out, {t.calls} call{t.calls === 1 ? "" : "s"}{t.cost ? `, about $${t.cost.toFixed(4)}` : ""}</span>
        </div>
        {d.status === "failed" ? <p role="alert" className="rounded-lg bg-red-50 p-3 body-6 text-red-700">{d.error}</p> : <p className="body-6">{d.summary}</p>}
      </Card>
      {d.status === "done" && (<>
        {!!d.notes.main_files?.length && d.notes.mode !== "design" && (
          <p role="note" className="rounded-lg bg-blue-50 p-2 caption-5 text-blue-800">Nothing in your request matched particular code, so it read the repository's main files: {d.notes.main_files.join(", ")}.</p>)}
        {d.notes.mode === "design" && d.notes.design ? <Design design={d.notes.design} titles={titles} copy={copy} /> : (
        <Card aria-label="Findings">
          <CardTitle>Findings ({d.findings.length})</CardTitle>
          {!d.findings.length ? <p className="body-6 text-muted">Nothing was found in the code it saw. That does not mean there is nothing to find.</p> : (
            <ul className="divide-y divide-line">{d.findings.map((f, i) => (
              <li key={i} className="py-3 body-6 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2"><Badge tone={TONE[f.severity] ?? "gray"}>{f.severity}</Badge><span className="font-medium">{f.title}</span>
                  <code className="caption-5 text-muted">{f.file}:{f.line}</code></div>
                <pre className="mt-2 overflow-auto rounded-md bg-slate-50 p-2 caption-5">{f.evidence}</pre>
                {f.explanation && <p className="mt-2">{f.explanation}</p>}
                {f.suggestion && <p className="mt-1 text-muted">{f.suggestion}</p>}
                {f.code && (<div className="mt-2">
                  <div className="flex items-center justify-between caption-5 text-muted"><span>Suggested code{f.code_ok === false ? " (does not parse, check it)" : ""}</span>
                    <Button size="sm" variant="ghost" aria-label={`Copy code for ${f.title}`} onClick={() => copy(f.code)}><Copy className="h-4 w-4" /></Button></div>
                  <pre className="overflow-auto rounded-md bg-slate-50 p-2 caption-5">{f.code}</pre></div>)}
              </li>))}</ul>)}
        </Card>)}
        {d.notes.dependencies && (
          <Card aria-label="Dependencies">
            <CardTitle>What it was told about other repositories</CardTitle>
            {!d.notes.dependencies.facts.length && !d.notes.dependencies.hidden ? <p className="body-6 text-muted">No other repository was found to use, or be used by, the code it read.</p> : (
              <ul className="space-y-2 body-6">{d.notes.dependencies.facts.map((f, i) => (
                <li key={i}><code>{f.path}</code> {f.direction === "used_by" ? "is used by" : "depends on"} <strong>{f.repo}</strong>
                  <span className="block caption-5 text-muted"><code>{f.from}</code> → <code>{f.to}</code>{f.evidence && ` — ${f.evidence}`}</span></li>))}</ul>)}
            {d.notes.dependencies.hidden > 0 && <p className="mt-2 caption-5 text-muted">{d.notes.dependencies.hidden} more link{d.notes.dependencies.hidden === 1 ? "" : "s"} involve repositories you cannot access; they are not described.</p>}
          </Card>)}
        <Card aria-label="Coverage">
          <CardTitle>What it saw</CardTitle>
          <ul className="space-y-0.5 caption-5">{d.notes.coverage.map((c) => <li key={c.file}><code>{c.file}</code> <span className="text-muted">{c.whole ? "whole file" : c.outline ? "its first lines and what it declares" : "only the parts around the match"}</span></li>)}</ul>
          {d.notes.dropped > 0 && <p className="mt-2 caption-5 text-muted">{d.notes.mode === "design" ? `${d.notes.dropped} part${d.notes.dropped === 1 ? " was" : "s were"} left out because ${d.notes.dropped === 1 ? "it named" : "they named"} no file that was shown, or ${d.notes.dropped === 1 ? "was" : "were"} not a plain diagram.` : `${d.notes.dropped} finding${d.notes.dropped === 1 ? " was" : "s were"} dropped because ${d.notes.dropped === 1 ? "it quoted" : "they quoted"} code that is not in the file, or a file that was not shown.`}</p>}
          <p className="mt-2 caption-5 text-muted">{d.notes.limit}</p>
        </Card>
      </>)}
    </>
  );
}

/** The architect's design overview: the high-level and low-level design as text, the components and flows with the files
 *  they come from, and a Mermaid diagram as text to paste into any Mermaid viewer. */
function Design({ design, titles, copy }: { design: PersonaDesign; titles?: PersonaOverview; copy: (text: string) => void }) {
  const first = titles?.first ?? "High-level design", second = titles?.second ?? "Low-level design";
  return (
    <>
      <Card aria-label={first}><CardTitle>{first}</CardTitle><Markdown text={design.hld} onOpen={() => {}} />
        {!!design.components.length && (
          <ul className="mt-3 space-y-2 body-6" aria-label="Components">{design.components.map((c) => (
            <li key={c.name} className="rounded-lg bg-slate-50 p-3"><span className="font-medium">{c.name}</span>{c.responsibility && <span className="text-muted"> — {c.responsibility}</span>}
              <div className="mt-1 flex flex-wrap gap-1">{c.files.map((f) => <code key={f} className="caption-5 text-muted">{f}</code>)}</div></li>))}</ul>)}
      </Card>
      {design.diagram && (
        <Card aria-label="Diagram">
          <CardTitle right={<Button size="sm" variant="ghost" aria-label="Copy the diagram" onClick={() => copy(design.diagram)}><Copy className="h-4 w-4" /></Button>}>Diagram (Mermaid)</CardTitle>
          <pre className="overflow-auto rounded-md bg-slate-50 p-2 caption-5">{design.diagram}</pre>
          <p className="mt-1 caption-5 text-muted">Paste it into a Mermaid viewer (mermaid.live, or a Markdown file on GitHub) to see it drawn.</p>
        </Card>)}
      <Card aria-label={second}><CardTitle>{second}</CardTitle><Markdown text={design.lld} onOpen={() => {}} />
        {!!design.flows.length && (
          <div className="mt-3 space-y-2 body-6" aria-label="Flows">{design.flows.map((f) => (
            <Panel key={f.name}><div className="font-medium">{f.name}</div>
              <ol className="ml-4 list-decimal">{f.steps.map((st, i) => <li key={i}>{st}</li>)}</ol>
              <div className="mt-1 flex flex-wrap gap-1">{f.files.map((x) => <code key={x} className="caption-5 text-muted">{x}</code>)}</div></Panel>))}</div>)}
      </Card>
      {!!design.not_covered.length && (
        <Card aria-label="Not covered"><CardTitle>What it could not tell</CardTitle>
          <ul className="list-disc space-y-0.5 pl-4 body-6">{design.not_covered.map((n, i) => <li key={i}>{n}</li>)}</ul></Card>)}
    </>
  );
}
