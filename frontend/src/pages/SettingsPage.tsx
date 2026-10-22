import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plug, SearchCheck, ShieldCheck, Brain } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { ConnectionCard, StatusPill, TINTS, selectCls } from "@/components/ConnectionCard";
import { useConfirm } from "@/components/feedback";
import IntegrationsPanel from "@/components/IntegrationsPanel";
import { Badge, Button, ErrorNote, Input, Label, PageHeader } from "@/components/ui";
import { api, type LLMSettings, type ModelInfo, type RetrievalStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const PROVIDERS = [
  ["heuristic", "Offline (no model calls)"],
  ["anthropic", "Anthropic Claude"],
  ["openai", "OpenAI-compatible (OpenAI, Azure, vLLM, Ollama…)"],
  ["github-models", "GitHub Models (retired 30 July 2026)"],
] as const;

const TABS = [["integrations", "Integrations", Plug], ["ai", "AI & search", Brain]] as const;

export default function SettingsPage() {
  const ask = useConfirm();
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["llm-settings"], queryFn: () => api<LLMSettings>("/settings/llm") });
  const [f, setF] = useState({ provider: "heuristic", model: "", base_url: "", api_key: "", input_price: "", output_price: "" });
  useEffect(() => {
    if (data) setF({ provider: data.provider, model: data.model, base_url: data.base_url, api_key: "", input_price: data.input_price?.toString() ?? "", output_price: data.output_price?.toString() ?? "" });
  }, [data]);

  const body = () => ({ provider: f.provider, model: f.model, base_url: f.base_url, api_key: f.api_key,
    input_price: f.input_price === "" ? null : Number(f.input_price), output_price: f.output_price === "" ? null : Number(f.output_price) });
  const save = useMutation({ mutationFn: () => api<LLMSettings>("/settings/llm", { method: "PUT", json: body() }),
    onSuccess: (d) => { qc.setQueryData(["llm-settings"], d); setF((x) => ({ ...x, api_key: "" })); test.reset(); } });
  const reset = useMutation({ mutationFn: () => api<LLMSettings>("/settings/llm", { method: "DELETE" }), onSuccess: (d) => qc.setQueryData(["llm-settings"], d) });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; error?: string; model?: string; latency_ms?: number; input_tokens?: number }>("/settings/llm/test", { method: "POST" }) });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  const catalog = useQuery({ queryKey: ["gh-models"], enabled: admin && f.provider === "github-models" && !!data?.has_key, retry: false,
    queryFn: () => api<ModelInfo[]>("/settings/llm/github-models/catalog") });
  const presets = f.provider === "github-models" && catalog.data?.length ? catalog.data.map((m) => m.id) : (data?.presets[f.provider] ?? []);
  const online = f.provider !== "heuristic";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "ai" ? "ai" : "integrations";

  return (
    <div>
      <PageHeader title="Settings" subtitle="Connect the tools your team already uses, and a language model if you want AI-written answers." />
      <div role="tablist" aria-label="Settings sections" className="mb-6 flex gap-1 border-b border-line">
        {TABS.map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setSp(k === "integrations" ? {} : { tab: k })}
            className={cn("-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm", tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink")}><Icon className="h-4 w-4" />{label}</button>))}
      </div>
      <div className="max-w-4xl">
        {tab === "integrations" ? <IntegrationsPanel admin={admin} /> : (
          <div className="space-y-4">
            <ConnectionCard icon={KeyRound} tint="violet" title="Language model"
              status={data ? <div className="flex items-center gap-1.5"><StatusPill on={!data.offline} onLabel="AI enabled" offLabel="offline mode" /><Badge tone="brand">{data.source === "org" ? "workspace setting" : "server default"}</Badge></div> : undefined}
              description="Choose a provider and paste a key. It is encrypted at rest and never shown again.">
              <form onSubmit={submit} className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block"><Label>Provider</Label>
                    <select disabled={!admin} className={selectCls} value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value, model: PROVIDERS.some(([p]) => p === e.target.value) ? (data?.presets[e.target.value]?.[0] ?? "") : "" })}>
                      {PROVIDERS.filter(([v]) => v !== "github-models" || data?.provider === "github-models").map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                  {online && <label className="block"><Label>Model</Label><Input disabled={!admin} required list="model-presets" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder={f.provider === "openai" ? "gpt-4o-mini" : "claude-sonnet-5-5"} />
                    <datalist id="model-presets">{presets.map((m) => <option key={m} value={m} />)}</datalist></label>}
                </div>
                {f.provider === "github-models" && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">GitHub retired GitHub Models on 30 July 2026, so its API no longer answers for anyone, whatever token you use. Pick another provider: Anthropic, an OpenAI-compatible service (Ollama, Gemini, Groq, OpenRouter, Azure AI Foundry) or offline mode. Your GitHub token still works for pull requests and private repositories.</p>}
                {online && (<>
                  {f.provider === "openai" && <label className="block"><Label>Base URL</Label><Input disabled={!admin} value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://api.openai.com/v1" /></label>}
                  <label className="block"><Label>{f.provider === "github-models" ? "GitHub token (optional)" : "API key"} {data?.has_key && <span className="text-emerald-700">({data.uses_github_token ? "using your connected GitHub account" : "stored — leave blank to keep"})</span>}</Label>
                    <Input disabled={!admin} type="password" autoComplete="off" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} placeholder={data?.has_key ? "••••••••••••" : "Paste your API key"} />
                    <span className="mt-1 block text-xs text-muted">{f.provider === "github-models" ? "Leave blank to use the GitHub connection below. Press “Test connection” to confirm the account can call GitHub Models." : f.provider === "anthropic" ? "Anthropic only supports API keys for apps (Claude subscription sign-in is limited to Claude Code and claude.ai). Create one in the Claude Console." : "Encrypted at rest. Never shown again and never sent to the browser."}</span></label>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block"><Label>Input price (USD / 1M tokens)</Label><Input disabled={!admin} type="number" min="0" step="0.01" value={f.input_price} onChange={(e) => setF({ ...f, input_price: e.target.value })} /></label>
                    <label className="block"><Label>Output price (USD / 1M tokens)</Label><Input disabled={!admin} type="number" min="0" step="0.01" value={f.output_price} onChange={(e) => setF({ ...f, output_price: e.target.value })} /></label>
                  </div>
                  <p className="text-xs text-muted">Prices drive the cost figures on the Token Usage page. Leave empty to report $0 (unpriced).</p>
                </>)}
                <ErrorNote error={save.error ?? reset.error} />
                {admin ? (
                  <div className="flex flex-wrap gap-2">
                    <Button disabled={save.isPending}>Save</Button>
                    <Button type="button" variant="outline" disabled={test.isPending || !data || data.offline} onClick={() => test.mutate()}>Test connection</Button>
                    {data?.source === "org" && <Button type="button" variant="ghost" onClick={async () => { if (await ask({ title: "Remove the stored key?", message: "The organisation reverts to the server defaults.", confirmLabel: "Remove key", danger: true })) reset.mutate(); }}>Reset</Button>}
                  </div>) : <p className="text-sm text-muted">Only workspace admins can change these settings.</p>}
                {test.data && <p role="status" className={`text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? `Connected — ${test.data.model}, ${test.data.latency_ms} ms (${test.data.input_tokens} input tokens)` : test.data.error}</p>}
              </form>
            </ConnectionCard>
            <RetrievalCard />
          </div>)}
      </div>
    </div>
  );
}

function RetrievalCard() {
  const { data } = useQuery({ queryKey: ["retrieval-status"], queryFn: () => api<RetrievalStatus>("/settings/retrieval") });
  if (!data) return null;
  return (
    <ConnectionCard icon={SearchCheck} tint="emerald" title="Search quality" description="How results are found and ranked. Set with environment variables on the server (REPOLENS_EMBEDDING_* and REPOLENS_RERANK_*)."
      status={<span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium", TINTS.slate)}><ShieldCheck className="h-3 w-3" />{data.embedding.offline ? "runs offline" : "uses a hosted embedder"}</span>}>
      <dl className="grid gap-4 text-sm sm:grid-cols-2">
        <div><dt className="text-xs text-muted">Embeddings</dt><dd className="font-medium">{data.embedding.model} <Badge tone={data.embedding.offline ? "gray" : "brand"}>{data.embedding.offline ? "offline" : data.embedding.provider}</Badge></dd>
          <dd className="text-xs text-muted">{data.embedding.dimensions} dimensions{data.embedding.offline ? " · works without internet, weaker on natural language" : ""}</dd></div>
        <div><dt className="text-xs text-muted">Reranker</dt><dd className="font-medium">{data.rerank.provider === "none" ? "Off" : data.rerank.model} {!data.rerank.configured && <Badge tone="amber">no endpoint set</Badge>}</dd>
          <dd className="text-xs text-muted">{data.rerank.provider === "none" ? "Enable with REPOLENS_RERANK_PROVIDER" : `re-scores the top ${data.rerank.depth} results of every search`}</dd></div>
      </dl>
    </ConnectionCard>
  );
}
