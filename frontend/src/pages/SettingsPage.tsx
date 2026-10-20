import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useConfirm, useToast } from "@/components/feedback";
import { KeyRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Card, CardTitle, ErrorNote, Input, Label, PageHeader } from "@/components/ui";
import GithubConnect from "@/components/GithubConnect";
import { api, type LLMSettings, type ModelInfo, type RetrievalStatus, type ScimStatus, type SsoSettings } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

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
        <RetrievalCard />
        {admin && <SsoCard />}
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

function RetrievalCard() {
  const { data } = useQuery({ queryKey: ["retrieval-status"], queryFn: () => api<RetrievalStatus>("/settings/retrieval") });
  if (!data) return null;
  return (
    <Card>
      <CardTitle>Search quality</CardTitle>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div><dt className="text-xs text-muted">Embeddings</dt><dd className="font-medium">{data.embedding.model} <Badge tone={data.embedding.offline ? "gray" : "brand"}>{data.embedding.offline ? "offline" : data.embedding.provider}</Badge></dd>
          <dd className="text-xs text-muted">{data.embedding.dimensions} dimensions{data.embedding.offline ? " · works without internet, weaker on natural language" : ""}</dd></div>
        <div><dt className="text-xs text-muted">Reranker</dt><dd className="font-medium">{data.rerank.provider === "none" ? "Off" : data.rerank.model} {!data.rerank.configured && <Badge tone="amber">no endpoint set</Badge>}</dd>
          <dd className="text-xs text-muted">{data.rerank.provider === "none" ? "Enable with REPOLENS_RERANK_PROVIDER" : `re-scores the top ${data.rerank.depth} results of every search`}</dd></div>
      </dl>
      <p className="mt-3 text-xs text-muted">Set with environment variables on the server: REPOLENS_EMBEDDING_* and REPOLENS_RERANK_*. See docs/features.md.</p>
    </Card>
  );
}

function SsoCard() {
  const qc = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const sso = useQuery({ queryKey: ["sso"], queryFn: () => api<SsoSettings>("/settings/sso") });
  const scim = useQuery({ queryKey: ["scim"], queryFn: () => api<ScimStatus>("/settings/scim") });
  const [f, setF] = useState({ issuer: "", client_id: "", client_secret: "", allowed_domains: "", default_role: "viewer", auto_provision: true, enabled: true });
  const [newToken, setNewToken] = useState("");
  useEffect(() => {
    const d = sso.data;
    if (d?.configured) setF({ issuer: d.issuer ?? "", client_id: d.client_id ?? "", client_secret: "", allowed_domains: (d.allowed_domains ?? []).join(", "), default_role: d.default_role ?? "viewer", auto_provision: d.auto_provision ?? true, enabled: d.enabled ?? true });
  }, [sso.data]);
  const save = useMutation({ mutationFn: () => api<SsoSettings>("/settings/sso", { method: "PUT", json: f }), onSuccess: (d) => { qc.setQueryData(["sso"], d); setF((x) => ({ ...x, client_secret: "" })); toast("Single sign-on saved"); } });
  const remove = useMutation({ mutationFn: () => api("/settings/sso", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["sso"] }); toast("Single sign-on removed"); } });
  const mint = useMutation({ mutationFn: () => api<{ token: string }>("/settings/scim/token", { method: "POST" }), onSuccess: (r) => { setNewToken(r.token); qc.invalidateQueries({ queryKey: ["scim"] }); } });
  const revoke = useMutation({ mutationFn: () => api("/settings/scim", { method: "DELETE" }), onSuccess: () => { setNewToken(""); qc.invalidateQueries({ queryKey: ["scim"] }); toast("SCIM token revoked"); } });
  const d = sso.data;
  return (
    <Card>
      <CardTitle right={d?.configured ? <Badge tone={d.enabled ? "green" : "gray"}>{d.enabled ? "enabled" : "disabled"}</Badge> : undefined}>Single sign-on and provisioning</CardTitle>
      <p className="mb-3 text-xs text-muted">Let people sign in with your identity provider (Okta, Microsoft Entra ID, Google Workspace and any OpenID Connect provider). SSO never grants admin: new users start as viewer or member.</p>
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        <div className="sm:col-span-2"><Label>Issuer URL</Label><Input aria-label="Issuer URL" value={f.issuer} onChange={(e) => setF({ ...f, issuer: e.target.value })} placeholder="https://login.microsoftonline.com/<tenant>/v2.0" /></div>
        <div><Label>Client id</Label><Input aria-label="Client id" value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })} /></div>
        <div><Label>Client secret</Label><Input aria-label="Client secret" type="password" autoComplete="off" value={f.client_secret} onChange={(e) => setF({ ...f, client_secret: e.target.value })} placeholder={d?.has_secret ? "•••••••• (unchanged)" : ""} /></div>
        <div className="sm:col-span-2"><Label>Allowed e-mail domains</Label><Input aria-label="Allowed e-mail domains" value={f.allowed_domains} onChange={(e) => setF({ ...f, allowed_domains: e.target.value })} placeholder="acme.com, acme.io" />
          <p className="mt-1 text-xs text-muted">Only these domains can sign in this way, and a domain can belong to one organisation.</p></div>
        <div><Label>New users get the role</Label><select aria-label="Default role" className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={f.default_role} onChange={(e) => setF({ ...f, default_role: e.target.value })}><option value="viewer">Viewer</option><option value="member">Member</option></select></div>
        <div className="flex items-end gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.auto_provision} onChange={(e) => setF({ ...f, auto_provision: e.target.checked })} />Create accounts on first sign-in</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />Enabled</label></div>
        {d && <p className="text-xs text-muted sm:col-span-2">Register this redirect URI with your provider: <code className="select-all rounded bg-slate-100 px-1">{d.redirect_uri}</code></p>}
        <div className="flex gap-2 sm:col-span-2"><Button disabled={save.isPending || !f.issuer || !f.client_id || !f.allowed_domains || (!d?.has_secret && !f.client_secret)}>Save</Button>
          {d?.configured && <Button type="button" variant="ghost" onClick={async () => { if (await ask({ title: "Remove single sign-on?", message: "People who signed in this way keep their accounts but can no longer use SSO.", confirmLabel: "Remove", danger: true })) remove.mutate(); }}>Remove</Button>}</div>
        <div className="sm:col-span-2"><ErrorNote error={save.error} /></div>
      </form>
      <div className="mt-5 border-t border-line pt-4">
        <h4 className="mb-1 text-sm font-semibold">Automatic provisioning (SCIM)</h4>
        <p className="mb-2 text-xs text-muted">Your identity provider creates, updates and deactivates users here. Deactivating a user ends their sessions immediately. Base URL: <code className="select-all rounded bg-slate-100 px-1">{window.location.origin}/scim/v2</code></p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" disabled={mint.isPending} onClick={async () => { if (!scim.data?.configured || await ask({ title: "Replace the SCIM token?", message: "The current token stops working immediately. Update it in your identity provider.", confirmLabel: "Generate new token" })) mint.mutate(); }}>{scim.data?.configured ? "Rotate token" : "Generate token"}</Button>
          {scim.data?.configured && <Button variant="ghost" size="sm" onClick={async () => { if (await ask({ title: "Revoke the SCIM token?", message: "Provisioning stops until you generate a new one.", confirmLabel: "Revoke", danger: true })) revoke.mutate(); }}>Revoke</Button>}
          {scim.data?.configured && <span className="text-xs text-muted">{scim.data.last_used_at ? `last used ${timeAgo(scim.data.last_used_at)}` : "not used yet"}</span>}
        </div>
        {newToken && <p role="status" className="mt-2 rounded-md bg-amber-50 p-2 text-xs text-amber-900">Copy this token now, it is not shown again: <code className="select-all break-all font-mono">{newToken}</code></p>}
      </div>
    </Card>
  );
}
