import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useConfirm } from "@/components/feedback";
import { KeyRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Card, CardTitle, ErrorNote, Input, Label, PageHeader } from "@/components/ui";
import GithubConnect from "@/components/GithubConnect";
import { api, type LLMSettings, type ModelInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const PROVIDERS = [
  ["heuristic", "Offline (no model calls)"],
  ["anthropic", "Anthropic Claude"],
  ["openai", "OpenAI-compatible (OpenAI, Azure, vLLM, Ollama…)"],
  ["github-models", "GitHub Models (uses your connected GitHub account)"],
] as const;

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

  return (
    <div>
      <PageHeader title="Settings" subtitle="Connect a language model for AI answers and root-cause narratives. Retrieval, graph and ranking always run locally." />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,520px)_1fr]">
        <Card>
          <CardTitle right={data && <div className="flex gap-1"><Badge tone={data.offline ? "gray" : "green"}>{data.offline ? "offline mode" : "AI enabled"}</Badge><Badge tone="brand">{data.source === "org" ? "workspace setting" : "server default"}</Badge></div>}>
            <span className="flex items-center gap-2"><KeyRound className="h-4 w-4" />Language model</span></CardTitle>
          <form onSubmit={submit} className="space-y-3">
            <label className="block"><Label>Provider</Label>
              <select disabled={!admin} className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value, model: PROVIDERS.some(([p]) => p === e.target.value) ? (data?.presets[e.target.value]?.[0] ?? "") : f.model })}>
                {PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            {online && (<>
              <label className="block"><Label>Model</Label><Input disabled={!admin} required list="model-presets" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder={f.provider === "openai" ? "gpt-4o-mini" : "claude-sonnet-5-5"} />
                <datalist id="model-presets">{presets.map((m) => <option key={m} value={m} />)}</datalist></label>
              {f.provider === "openai" && <label className="block"><Label>Base URL</Label><Input disabled={!admin} value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://api.openai.com/v1" /></label>}
              <label className="block"><Label>{f.provider === "github-models" ? "GitHub token (optional)" : "API key"} {data?.has_key && <span className="text-emerald-700">({data.uses_github_token ? "using your connected GitHub account" : "stored — leave blank to keep"})</span>}</Label>
                <Input disabled={!admin} type="password" autoComplete="off" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} placeholder={data?.has_key ? "••••••••••••" : "Paste your API key"} />
                <span className="mt-1 block text-xs text-muted">{f.provider === "github-models" ? "Leave blank to use the GitHub connection below. Press “Test connection” to confirm the account can call GitHub Models." : f.provider === "anthropic" ? "Anthropic only supports API keys for apps (Claude subscription sign-in is limited to Claude Code and claude.ai). Create one in the Claude Console." : "Encrypted at rest. Never shown again and never sent to the browser."}</span></label>
              <div className="grid grid-cols-2 gap-3">
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
        </Card>
        <GithubConnect admin={admin} />
        <Card className="lg:col-start-2 lg:row-start-1 lg:row-span-2">
          <CardTitle>What uses the model</CardTitle>
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-700">
            <li><b>Investigations</b> — the root-cause narrative and suggested next steps (ranking and citation checks stay deterministic).</li>
            <li><b>Knowledge “Ask”</b> — answers written from retrieved passages, with [S#] citations that are checked against the sources.</li>
            <li>Secrets in prompts are redacted, results are cached, and daily/monthly budgets stop optional calls when exceeded.</li>
          </ul>
          <div className="mt-4 rounded-md bg-slate-50 p-3 text-xs text-muted">Embeddings use <code>{data?.embedding_provider ?? "…"}</code> and are configured on the server (<code>REPOLENS_EMBEDDING_PROVIDER</code>); changing them requires re-indexing.</div>
        </Card>
      </div>
    </div>
  );
}
