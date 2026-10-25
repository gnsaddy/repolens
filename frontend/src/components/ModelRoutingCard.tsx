import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Copy, Download, Layers, Search, Sparkles, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ConnectionCard, StatusPill, selectCls } from "@/components/ConnectionCard";
import { useConfirm, useToast } from "@/components/feedback";
import Modal from "@/components/Modal";
import { providerLabel } from "@/components/LanguageModelCard";
import { Badge, Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type MemoryEntry, type MemoryStats, type ModelRegistry, type SkillFile, type Tier } from "@/lib/api";
import { timeAgo } from "@/lib/utils";
import { cn } from "@/lib/utils";

const TIERS: [Tier, string][] = [["light", "Light — quick, cheap"], ["standard", "Standard"], ["strong", "Strong — hard problems"]];
const PROVIDERS = [["gemini", "Google Gemini"], ["anthropic", "Anthropic Claude"], ["openai", "OpenAI-compatible"]] as const;
const guessTier = (id: string): Tier => (/(^|[-_.:/])(lite|mini|nano|small|haiku|flash-8b)([-_.:/]|$)/i.test(id) ? "light" : /(^|[-_.:/])(pro|opus|ultra|large|o1|o3)([-_.:/]|$)/i.test(id) ? "strong" : "standard");
const tierTone = (t: Tier) => (t === "light" ? "green" : t === "strong" ? "brand" : "gray") as "green" | "brand" | "gray";

/** Several models and providers at once: each task goes to the cheapest model that can do it. */
export default function ModelRoutingCard({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const { data } = useQuery({ queryKey: ["model-registry"], queryFn: () => api<ModelRegistry>("/settings/models") });
  const [open, setOpen] = useState(false);
  const put = (d: ModelRegistry) => qc.setQueryData(["model-registry"], d);
  const savings = useMutation({ mutationFn: (v: boolean) => api<ModelRegistry>("/settings/routing", { method: "PUT", json: { savings_mode: v } }), onSuccess: put });
  const patch = useMutation({ mutationFn: (v: { id: string; tier?: Tier; enabled?: boolean }) => api<ModelRegistry>(`/settings/models/${v.id}`, { method: "PUT", json: { tier: v.tier, enabled: v.enabled } }), onSuccess: put });
  const del = useMutation({ mutationFn: (id: string) => api<ModelRegistry>(`/settings/models/${id}`, { method: "DELETE" }), onSuccess: put });
  const delConn = useMutation({ mutationFn: (id: string) => api<ModelRegistry>(`/settings/models/connections/${id}`, { method: "DELETE" }), onSuccess: (d) => { put(d); toast("Provider removed"); } });
  if (!data?.connections) return null;

  return (
    <>
      <ConnectionCard icon={Layers} tint="indigo" title="Models and routing" status={<StatusPill on={data.active > 0} onLabel={`${data.active} model${data.active === 1 ? "" : "s"} active`} offLabel="No model" />}
        description="Add several models, even from different providers. Each task uses the cheapest one that can do it, and moves up only when it is hard."
        footer={admin ? <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Add models</Button> : undefined}>
        {data.connections.length === 0 ? <p className="text-sm text-muted">{admin ? "Add a model to get AI-written answers; everything also works without one." : "No models configured."}</p> : (
          <div className="space-y-4">
            {data.connections.map((c) => (
              <section key={c.id} aria-label={`${providerLabel(c.provider)} models`} className="rounded-lg border border-line">
                <header className="flex items-center justify-between bg-slate-50 px-3 py-2 text-sm">
                  <span className="font-medium">{providerLabel(c.provider)}{c.base_url && <span className="ml-2 text-xs text-muted">{c.base_url}</span>}</span>
                  {admin && !c.legacy && <button className="text-xs text-red-600 hover:underline" onClick={async () => { if (await ask({ title: `Remove ${providerLabel(c.provider)}?`, message: "Its key and its models are deleted. Tasks move to your other models.", confirmLabel: "Remove", danger: true })) delConn.mutate(c.id); }}>Remove provider</button>}
                </header>
                <ul className="divide-y divide-line">
                  {c.models.map((m) => (
                    <li key={m.id} className={cn("flex flex-wrap items-center gap-3 px-3 py-2 text-sm", !m.enabled && "opacity-60")}>
                      <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium">{m.model}</code>
                      {admin && !c.legacy ? (<>
                        <select aria-label={`Tier for ${m.model}`} className={cn(selectCls, "h-8 w-auto text-xs")} value={m.tier} onChange={(e) => patch.mutate({ id: m.id, tier: e.target.value as Tier })}>{TIERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                        <label className="flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={m.enabled} onChange={(e) => patch.mutate({ id: m.id, enabled: e.target.checked })} />In use</label>
                        <button aria-label={`Remove ${m.model}`} className="ml-auto text-muted hover:text-red-600" onClick={() => del.mutate(m.id)}><Trash2 className="h-4 w-4" /></button>
                      </>) : <Badge tone={tierTone(m.tier)}>{m.tier}</Badge>}
                    </li>))}
                </ul>
              </section>))}
            <label className="flex items-start gap-3 rounded-lg bg-emerald-50 p-3 text-sm">
              <input type="checkbox" className="mt-1" checked={data.savings_mode} disabled={!admin || savings.isPending} onChange={(e) => savings.mutate(e.target.checked)} />
              <span><span className="font-medium">Savings mode</span><span className="block text-xs text-muted">Plain tasks use the next cheaper tier. Long, multi-repository or low-confidence work moves up. Off: every task uses its normal tier.</span></span>
            </label>
            {data.active > 1 && (
              <table className="w-full text-sm" aria-label="Routing plan">
                <thead><tr className="text-left text-xs text-muted"><th className="py-1 font-normal">Task</th><th className="font-normal">Model now</th><th className="font-normal">Why</th></tr></thead>
                <tbody>{data.plan.map((r) => <tr key={r.kind} className="border-t border-line"><td className="py-1.5">{r.label}</td><td><code className="text-xs">{r.model ?? "—"}</code> <Badge tone={tierTone(r.tier)}>{r.tier}</Badge></td><td className="text-xs text-muted">{r.why}</td></tr>)}</tbody>
              </table>)}
          </div>)}
        <ErrorNote error={patch.error ?? del.error ?? savings.error} />
      </ConnectionCard>
      {open && <Modal title="Add models" subtitle="Test the key, tick the models to use and give each a tier." onClose={() => setOpen(false)}><AddModels data={data} onDone={(d) => { put(d); setOpen(false); toast("Models added"); }} /></Modal>}
    </>
  );
}

function AddModels({ data, onDone }: { data: ModelRegistry; onDone: (d: ModelRegistry) => void }) {
  const [provider, setProvider] = useState("gemini");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[] | null>(null);
  const [picked, setPicked] = useState<Record<string, Tier>>({});
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState("");
  const [results, setResults] = useState<Record<string, { ok: boolean; text: string }>>({});
  const [testing, setTesting] = useState(false);
  const stored = data.connections.some((c) => c.provider === provider && !c.legacy && c.has_key);
  const check = useMutation({
    mutationFn: () => api<{ ok: boolean; error?: string; models?: { id: string }[] }>("/settings/llm/check", { method: "POST", json: { provider, api_key: apiKey, base_url: baseUrl, model: "" } }),
    onSuccess: (r) => { if (r.ok) { setErr(""); setModels((r.models ?? []).map((m) => m.id)); } else { setErr(r.error ?? "Failed"); setModels(null); } },
  });
  const save = useMutation({
    mutationFn: () => api<ModelRegistry>("/settings/models/connections", { method: "POST", json: { provider, api_key: apiKey, base_url: baseUrl, models: Object.entries(picked).map(([model, tier]) => ({ model, tier })) } }),
    onSuccess: onDone,
  });
  const reset = () => { setModels(null); setPicked({}); setErr(""); setResults({}); };
  const testAll = async () => {
    setTesting(true);
    for (const m of Object.keys(picked)) {
      if (results[m]?.ok) continue;
      try {
        const r = await api<{ ok: boolean; error?: string; latency_ms?: number }>("/settings/llm/check", { method: "POST", json: { provider, api_key: apiKey, base_url: baseUrl, model: m } });
        setResults((x) => ({ ...x, [m]: { ok: r.ok, text: r.ok ? `works, ${r.latency_ms} ms` : r.error ?? "Failed" } }));
      } catch (e) { setResults((x) => ({ ...x, [m]: { ok: false, text: (e as Error).message } })); }
    }
    setTesting(false);
  };
  const shown = (models ?? []).filter((m) => m.toLowerCase().includes(filter.toLowerCase()));
  const count = Object.keys(picked).length;
  const allOk = count > 0 && Object.keys(picked).every((m) => results[m]?.ok);
  return (
    <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); if (count) save.mutate(); }}>
      <label className="block"><Label>Provider</Label><select className={selectCls} value={provider} onChange={(e) => { setProvider(e.target.value); reset(); }}>{PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      {provider === "openai" && <label className="block"><Label>Base URL</Label><Input value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); reset(); }} placeholder="https://api.openai.com/v1" /></label>}
      <label className="block"><Label>API key {stored && <span className="text-emerald-700">(stored — leave blank to keep)</span>}</Label>
        <Input type="password" autoComplete="off" value={apiKey} onChange={(e) => { setApiKey(e.target.value); reset(); }} placeholder={stored ? "••••••••••••" : "Paste your API key"} /></label>
      <Button type="button" variant={models ? "outline" : "primary"} disabled={check.isPending || (!apiKey && !stored)} onClick={() => check.mutate()}>{check.isPending ? "Testing…" : "Test connection"}</Button>
      {err && <p role="alert" className="text-sm text-red-600">{err}</p>}
      {models && (
        <div className="space-y-2 border-t border-line pt-3">
          <p role="status" className="text-sm text-emerald-700">Key accepted. Tick the models to use, then test them.</p>
          <Input aria-label="Filter models" placeholder="Filter models…" className="h-8" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <ul className="max-h-56 space-y-1 overflow-auto">{shown.map((m) => (
            <li key={m} className="flex items-center gap-2 text-sm">
              <label className="flex flex-1 items-center gap-2"><input type="checkbox" checked={m in picked} onChange={(e) => { setPicked((p) => { const n = { ...p }; if (e.target.checked) n[m] = guessTier(m); else delete n[m]; return n; }); setResults((x) => { const n = { ...x }; delete n[m]; return n; }); }} /><code className="text-xs">{m}</code></label>
              {results[m] && <span role="status" className={cn("text-xs", results[m].ok ? "text-emerald-700" : "text-red-600")}>{results[m].ok ? "✓ " : "✗ "}{results[m].text}</span>}
              {m in picked && <select aria-label={`Tier for ${m}`} className={cn(selectCls, "h-7 w-auto text-xs")} value={picked[m]} onChange={(e) => setPicked((p) => ({ ...p, [m]: e.target.value as Tier }))}>{TIERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}
            </li>))}</ul>
          {count > 0 && <Button type="button" variant={allOk ? "outline" : "primary"} disabled={testing || allOk} onClick={testAll}>{testing ? "Testing…" : allOk ? "All models work" : `Test ${count} selected model${count === 1 ? "" : "s"}`}</Button>}
          <p className="text-xs text-muted">Tiers are guessed from the name; change them if needed. Cheap models take simple tasks, strong ones take hard tasks.</p>
        </div>)}
      <ErrorNote error={save.error} />
      <div className="flex justify-end"><Button disabled={!allOk || save.isPending} title={allOk ? "" : "Test the selected models first"}>{count ? `Add ${count} model${count === 1 ? "" : "s"}` : "Add models"}</Button></div>
    </form>
  );
}

/** What the local answer memory has saved. */
export function MemoryCard({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["answer-memory"], queryFn: () => api<MemoryStats>("/settings/memory") });
  const clear = useMutation({ mutationFn: () => api<{ cleared: number }>("/settings/memory", { method: "DELETE" }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["answer-memory"] }); toast(`Forgot ${r.cleared} answers`); } });
  const [browse, setBrowse] = useState(false);
  if (data?.entries === undefined) return null;
  return (<>
    <ConnectionCard icon={Brain} tint="violet" title="Answer memory" status={<StatusPill on={data.enabled} onLabel="On" offLabel="Off" />}
      description="Answers are remembered on this server. A repeated or re-worded question is answered from memory, with no model call and no tokens, until the underlying docs or code change."
      footer={data.entries > 0 ? (<>
        <Button size="sm" onClick={() => setBrowse(true)}><Search className="h-3.5 w-3.5" />Browse and search memory</Button>
        {admin && <Button size="sm" variant="outline" onClick={async () => { if (await ask({ title: "Forget all remembered answers?", message: "Questions will be sent to the model again.", confirmLabel: "Forget", danger: true })) clear.mutate(); }}>Clear memory</Button>}</>) : undefined}>
      <dl className="grid gap-4 text-sm sm:grid-cols-3">
        <div><dt className="text-xs text-muted">Remembered answers</dt><dd className="font-medium">{data.entries}</dd></div>
        <div><dt className="text-xs text-muted">Times reused</dt><dd className="font-medium">{data.hits}</dd></div>
        <div><dt className="text-xs text-muted">Tokens saved</dt><dd className="font-medium">{data.tokens_saved.toLocaleString()}</dd></div>
      </dl>
      <p className="mt-2 text-xs text-muted">Matches questions that are {Math.round(data.threshold * 100)}% similar, kept {data.ttl_hours} hours, never shared between people with different repository access.</p>
    </ConnectionCard>
    {browse && <Modal wide title="Answer memory" subtitle="Everything RepoLens has answered and kept. Search questions and answers, then turn what you find into a skill." onClose={() => setBrowse(false)}><MemoryBrowser admin={admin} /></Modal>}
  </>);
}

/** Look through remembered answers (deep search reads the answers too) and package a selection as a Claude skill. */
function MemoryBrowser({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [deep, setDeep] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [skill, setSkill] = useState<SkillFile | null>(null);
  const list = useQuery({ queryKey: ["memory-entries", q, deep], queryFn: () => api<MemoryEntry[]>(`/settings/memory/entries?q=${encodeURIComponent(q)}&deep=${deep}`) });
  const forget = useMutation({ mutationFn: (id: string) => api(`/settings/memory/entries/${id}`, { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["memory-entries"] }); qc.invalidateQueries({ queryKey: ["answer-memory"] }); toast("Forgotten"); } });
  const make = useMutation({ mutationFn: () => api<SkillFile>("/settings/memory/skill", { method: "POST", json: { entry_ids: [...picked] } }), onSuccess: setSkill });
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const download = (f: SkillFile) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([f.markdown], { type: "text/markdown" })); a.download = "SKILL.md"; a.click(); URL.revokeObjectURL(a.href); };
  if (skill) return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Save this as <code>{skill.filename}</code> in a <code>.claude/skills</code> folder (or upload it as a skill in Claude). It contains {skill.entries} answer{skill.entries === 1 ? "" : "s"}; secrets were removed.</p>
      <pre aria-label="Skill file" className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-xs">{skill.markdown}</pre>
      <div className="flex gap-2"><Button size="sm" onClick={() => download(skill)}><Download className="h-3.5 w-3.5" />Download SKILL.md</Button>
        <Button size="sm" variant="outline" onClick={() => { void navigator.clipboard?.writeText(skill.markdown); toast("Copied"); }}><Copy className="h-3.5 w-3.5" />Copy</Button>
        <Button size="sm" variant="ghost" onClick={() => setSkill(null)}>Back</Button></div>
    </div>);
  return (
    <div className="space-y-3">
      <form className="flex flex-wrap items-center gap-2" role="search" onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }}>
        <Input aria-label="Search memory" className="min-w-56 flex-1" placeholder="Search questions and answers…" value={text} onChange={(e) => { setText(e.target.value); if (!e.target.value) setQ(""); }} />
        <label className="flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} />Deep: also read answers</label>
        <Button variant="outline" size="sm">Search</Button>
      </form>
      {list.isLoading ? null : !list.data?.length ? <p className="py-6 text-center text-sm text-muted">{q ? `Nothing in memory matches “${q}”.` : "Nothing is remembered yet."}</p> : (
        <ul aria-label="Remembered answers" className="space-y-2">{list.data.map((e) => (
          <li key={e.id} className="rounded-lg border border-line p-3 text-sm">
            <div className="flex items-start gap-2">
              <input type="checkbox" aria-label={`Select ${e.asked}`} className="mt-1" checked={picked.has(e.id)} onChange={() => toggle(e.id)} />
              <div className="min-w-0 flex-1">
                <div className="font-medium">{e.asked}</div>
                <div className="text-xs text-muted">{timeAgo(e.created_at)} · reused {e.hits}× · {e.tokens.toLocaleString()} tokens to produce{e.score != null ? ` · ${Math.round(e.score * 100)}% match in the ${e.matched}` : ""}</div>
                <details className="mt-1"><summary className="cursor-pointer text-xs text-brand">Show answer</summary><p className="mt-1 whitespace-pre-wrap leading-6">{e.answer}</p>{!!e.sources.length && <p className="mt-1 text-xs text-muted">Sources: {e.sources.join(", ")}</p>}</details>
              </div>
              {admin && <button aria-label={`Forget ${e.asked}`} className="text-muted hover:text-red-600" onClick={() => forget.mutate(e.id)}><Trash2 className="h-4 w-4" /></button>}
            </div>
          </li>))}</ul>)}
      <ErrorNote error={forget.error ?? make.error} />
      <div className="sticky bottom-0 flex items-center justify-between border-t border-line bg-white pt-3">
        <span className="text-xs text-muted">{picked.size} selected</span>
        <Button disabled={!picked.size || make.isPending} onClick={() => make.mutate()}><Sparkles className="h-4 w-4" />{make.isPending ? "Building…" : "Create skill from selected"}</Button>
      </div>
    </div>
  );
}
