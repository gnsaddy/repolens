import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ConnectionCard, StatusPill, selectCls } from "@/components/ConnectionCard";
import { useConfirm, useToast } from "@/components/feedback";
import Modal from "@/components/Modal";
import { Badge, Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type LLMSettings } from "@/lib/api";
import { cn } from "@/lib/utils";

const PROVIDERS = [
  ["gemini", "Google Gemini"],
  ["anthropic", "Anthropic Claude"],
  ["openai", "OpenAI-compatible (OpenAI, Azure, vLLM, Ollama…)"],
] as const;
export const providerLabel = (p: string) => PROVIDERS.find(([v]) => v === p)?.[1].split(" (")[0] ?? (p === "github-models" ? "GitHub Models (retired)" : p);

interface CheckResult { ok: boolean; error?: string; models?: { id: string; name: string }[]; model?: string; latency_ms?: number; input_tokens?: number }
const KEY_HELP: Record<string, string> = {
  gemini: "Create a key at aistudio.google.com/apikey. The free tier is rate limited and Google may use free-tier requests to improve its products, so avoid it for private code.",
  anthropic: "Anthropic only supports API keys for apps (Claude subscription sign-in is limited to Claude Code and claude.ai). Create one in the Claude Console.",
  openai: "Any service that speaks the OpenAI chat-completions protocol. Enter its base URL and key.",
};

/** The language model as an integration: a summary when connected, and a guided card (test key, pick model, test model, save) to set it up. */
export default function LanguageModelCard({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["llm-settings"], queryFn: () => api<LLMSettings>("/settings/llm") });
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const test = useMutation({
    mutationFn: () => api<{ ok: boolean; error?: string; model?: string; latency_ms?: number; input_tokens?: number }>("/settings/llm/test", { method: "POST" }),
    onSuccess: (r) => setNote(r.ok ? { ok: true, text: `Replied in ${r.latency_ms} ms (${r.input_tokens} input tokens)` } : { ok: false, text: r.error ?? "Failed" }),
    onError: (e) => setNote({ ok: false, text: (e as Error).message }),
  });
  const reset = useMutation({ mutationFn: () => api<LLMSettings>("/settings/llm", { method: "DELETE" }), onSuccess: (d) => { qc.setQueryData(["llm-settings"], d); setNote(null); toast("Language model disconnected"); } });
  const on = !!data && !data.offline;

  return (
    <>
      <ConnectionCard icon={KeyRound} tint="violet" title="Language model" status={data ? <StatusPill on={on} onLabel="Connected" offLabel="Offline" /> : undefined}
        description="Optional. Everything works offline; a model makes answers and root-cause write-ups read better."
        footer={admin ? (on ? (<>
          <Button size="sm" variant="outline" disabled={test.isPending} onClick={() => { setNote(null); test.mutate(); }}>Test connection</Button>
          <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Change model</Button>
          {data?.source === "org" && <Button size="sm" variant="danger" disabled={reset.isPending} onClick={async () => { if (await ask({ title: "Disconnect the language model?", message: "The stored key is deleted and answers fall back to the server default (or offline mode).", confirmLabel: "Disconnect", danger: true })) reset.mutate(); }}>Disconnect</Button>}
          {note && <span role="status" className={cn("text-sm", note.ok ? "text-emerald-700" : "text-red-600")}>{note.text}</span>}</>
        ) : <Button onClick={() => setOpen(true)}>Connect a language model</Button>) : undefined}>
        {on && data ? (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <div><dt className="text-xs text-muted">Provider</dt><dd className="font-medium">{providerLabel(data.provider)}</dd></div>
            <div><dt className="text-xs text-muted">Model in use</dt><dd><code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium">{data.model}</code></dd></div>
            <div><dt className="text-xs text-muted">Key</dt><dd className="flex items-center gap-1.5 font-medium">stored<Badge tone="brand">{data.source === "org" ? "workspace setting" : "server default"}</Badge></dd></div>
            {data.provider === "github-models" && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 sm:col-span-3">GitHub retired GitHub Models on 30 July 2026, so this no longer answers. Choose Change model and pick another provider.</p>}
          </dl>
        ) : <p className="text-sm text-muted">{admin ? "Not connected: answers and reports use the built-in rules." : "Only workspace admins can connect a language model."}</p>}
      </ConnectionCard>
      {open && <Modal title={on ? "Change language model" : "Connect a language model"} subtitle="Test your key, choose a model, test it, then save." onClose={() => setOpen(false)}><Wizard data={data} onDone={() => setOpen(false)} /></Modal>}
    </>
  );
}

function Wizard({ data, onDone }: { data?: LLMSettings; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const connected = !!data && !data.offline && PROVIDERS.some(([v]) => v === data.provider);
  const [provider, setProvider] = useState<string>(connected ? data!.provider : "gemini");
  const [baseUrl, setBaseUrl] = useState(connected && data!.provider === "openai" ? data!.base_url : "");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<{ id: string; name: string }[] | null>(null);
  const [manual, setManual] = useState(false);
  const [model, setModel] = useState("");
  const [filter, setFilter] = useState("");
  const [ping, setPing] = useState<CheckResult | null>(null);
  const [input, setInput] = useState(data?.input_price?.toString() ?? "");
  const [output, setOutput] = useState(data?.output_price?.toString() ?? "");
  const usesStored = connected && data!.provider === provider && !apiKey;

  const reset = () => { setModels(null); setManual(false); setModel(""); setPing(null); setCheckErr(""); };
  const [checkErr, setCheckErr] = useState("");
  const check = useMutation({
    mutationFn: (m: string) => api<CheckResult>("/settings/llm/check", { method: "POST", json: { provider, api_key: apiKey, base_url: baseUrl, model: m } }),
  });
  const listModels = () => check.mutate("", { onSuccess: (r) => { setPing(null); setModel(""); if (r.ok) { setCheckErr(""); setModels(r.models ?? []); setManual(false); } else { setCheckErr(r.error ?? "Failed"); setModels(null); } } });
  const testModel = () => check.mutate(model, { onSuccess: (r) => setPing(r) });
  const save = useMutation({
    mutationFn: () => api<LLMSettings>("/settings/llm", { method: "PUT", json: { provider, model, base_url: baseUrl, api_key: apiKey, input_price: input === "" ? null : Number(input), output_price: output === "" ? null : Number(output) } }),
    onSuccess: (d) => { qc.setQueryData(["llm-settings"], d); toast(`Using ${model}`); onDone(); },
  });
  const step = ping?.ok ? 3 : models || manual ? 2 : 1;
  const filtered = (models ?? []).filter((m) => `${m.id} ${m.name}`.toLowerCase().includes(filter.toLowerCase()));

  return (
    <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); if (ping?.ok) save.mutate(); }}>
      <ol className="flex gap-2 text-xs" aria-label="Steps">
        {["Test your key", "Choose a model", "Test and save"].map((l, i) => (
          <li key={l} className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1", step === i + 1 ? "bg-indigo-50 font-medium text-brand" : step > i + 1 ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-muted")}>
            {step > i + 1 ? <Check className="h-3 w-3" /> : <span>{i + 1}</span>}{l}</li>))}
      </ol>
      <div className="grid gap-3">
        <label className="block"><Label>Provider</Label>
          <select className={selectCls} value={provider} onChange={(e) => { setProvider(e.target.value); reset(); }}>{PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        {provider === "openai" && <label className="block"><Label>Base URL</Label><Input value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); reset(); }} placeholder="https://api.openai.com/v1" /></label>}
        <label className="block"><Label>API key {usesStored && <span className="text-emerald-700">(stored — leave blank to keep)</span>}</Label>
          <Input type="password" autoComplete="off" value={apiKey} onChange={(e) => { setApiKey(e.target.value); reset(); }} placeholder={usesStored ? "••••••••••••" : "Paste your API key"} />
          <span className="mt-1 block text-xs text-muted">{KEY_HELP[provider]} The key is sent only to the provider and stored encrypted.</span></label>
        <div><Button type="button" variant={models || manual ? "outline" : "primary"} disabled={check.isPending || (!apiKey && !usesStored)} onClick={listModels}>{check.isPending && !model ? "Testing…" : "Test connection"}</Button></div>
        {checkErr && <p role="alert" className="text-sm text-red-600">{checkErr}</p>}
        <ErrorNote error={check.error} />
      </div>

      {(models || manual) && (
        <div className="space-y-2 border-t border-line pt-4">
          {models && <p role="status" className="text-sm text-emerald-700">Key accepted. {models.length} models available.</p>}
          {models && models.length > 0 && (<div aria-label="Available models">
            <Input aria-label="Filter models" placeholder="Filter models…" className="mb-2 h-8" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <ul className="flex max-h-44 flex-wrap gap-1.5 overflow-auto">{filtered.map((m) => (
              <li key={m.id}><button type="button" title={m.name || m.id} aria-pressed={model === m.id} onClick={() => { setModel(m.id); setPing(null); }}
                className={cn("rounded-full border px-2.5 py-1 text-xs", model === m.id ? "border-brand bg-indigo-50 font-medium text-brand" : "border-line bg-white hover:border-brand")}>{m.id}</button></li>))}</ul></div>)}
          {(manual || !models?.length) && <label className="block"><Label>Model name</Label><Input aria-label="Model name" value={model} onChange={(e) => { setModel(e.target.value); setPing(null); }} placeholder="e.g. gpt-4o-mini" /></label>}
          {!manual && !!models?.length && <button type="button" className="text-xs text-brand hover:underline" onClick={() => setManual(true)}>Type a model name instead</button>}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="outline" disabled={!model || check.isPending} onClick={testModel}>{check.isPending && model ? "Testing…" : model ? `Test ${model}` : "Choose a model to test"}</Button>
            {ping && <span role="status" className={cn("text-sm", ping.ok ? "text-emerald-700" : "text-red-600")}>{ping.ok ? `Works — replied in ${ping.latency_ms} ms (${ping.input_tokens} input tokens)` : ping.error}</span>}
          </div>
        </div>)}

      <details className="text-sm"><summary className="cursor-pointer text-muted">Cost tracking (optional)</summary>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <label className="block"><Label>Input price (USD / 1M tokens)</Label><Input type="number" min="0" step="0.01" value={input} onChange={(e) => setInput(e.target.value)} /></label>
          <label className="block"><Label>Output price (USD / 1M tokens)</Label><Input type="number" min="0" step="0.01" value={output} onChange={(e) => setOutput(e.target.value)} /></label>
        </div><p className="mt-1 text-xs text-muted">Prices drive the cost figures on the Token Usage page. Leave empty to report $0 (unpriced).</p></details>
      <ErrorNote error={save.error} />
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onDone}>Cancel</Button><Button disabled={!ping?.ok || save.isPending} title={ping?.ok ? "" : "Test the model first"}>Save</Button></div>
    </form>
  );
}
