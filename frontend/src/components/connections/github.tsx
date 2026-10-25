import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { safeHref } from "@/lib/utils";
import { Copy, ExternalLink } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { selectCls } from "@/components/ConnectionCard";
import { useToast } from "@/components/feedback";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type DevicePoll, type DeviceStart, type GithubConn } from "@/lib/api";
import type { Integration } from "./types";
import { useCheck } from "./useCheck";

const SCOPES = [
  ["repo read:user", "Private + public repositories"],
  ["public_repo read:user", "Public repositories only"],
] as const;

export function useGithub(): Integration {
  const qc = useQueryClient();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["github-conn"], queryFn: () => api<GithubConn>("/github/connection") });
  const remove = useMutation({ mutationFn: () => api("/github/connection", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["github-conn"] }); qc.invalidateQueries({ queryKey: ["llm-settings"] }); toast("GitHub disconnected"); } });
  const c = conn.data;
  return {
    connected: !!c?.connected, loading: conn.isLoading, account: c?.login ? `@${c.login}` : undefined,
    detail: c?.connected ? `${c.auth_method === "oauth" ? `OAuth${c.login ? ` · @${c.login}` : ""}` : "Personal access token"} · ${c.api_base}` : "",
    test: async () => { const r = await api<{ ok: boolean; user?: string; error?: string }>("/github/test", { method: "POST" }); if (r.ok) qc.invalidateQueries({ queryKey: ["github-conn"] }); return r.ok ? `Connected as ${r.user}` : { error: r.error ?? "Failed" }; },
    disconnect: () => remove.mutateAsync(),
    disconnectMessage: "The stored token is deleted. Also revoke the app at github.com/settings/applications.",
  };
}

/** GitHub sign-in via the OAuth device flow (no redirect URI, no client secret, token never reaches the browser), or a personal access token. */
export function GithubForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["github-conn"], queryFn: () => api<GithubConn>("/github/connection") });
  const [clientId, setClientId] = useState("");
  const [scope, setScope] = useState<string>(SCOPES[0][0]);
  const [flow, setFlow] = useState<DeviceStart | null>(null);
  const [outcome, setOutcome] = useState<string>("");
  const [showToken, setShowToken] = useState(true);
  const [token, setToken] = useState("");
  const [apiBase, setApiBase] = useState("https://api.github.com");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => { if (conn.data?.oauth_client_id && !clientId) setClientId(conn.data.oauth_client_id); }, [conn.data, clientId]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const finished = () => { qc.invalidateQueries({ queryKey: ["github-conn"] }); qc.invalidateQueries({ queryKey: ["llm-settings"] }); toast("GitHub connected"); onDone(); };
  const poll = (f: DeviceStart, interval: number) => {
    timer.current = setTimeout(async () => {
      try {
        const r = await api<DevicePoll>("/github/oauth/device/poll", { method: "POST", json: { flow_id: f.flow_id } });
        if (r.status === "pending") return poll(f, r.interval ?? interval);
        setFlow(null);
        setOutcome(r.status === "success" ? "" : r.status === "denied" ? "Authorisation was denied on GitHub." : r.status === "expired" ? "The code expired — start again." : r.error ?? "GitHub returned an error.");
        if (r.status === "success") finished();
      } catch (e) { setFlow(null); setOutcome((e as Error).message); }
    }, interval * 1000);
  };
  const start = useMutation({
    mutationFn: () => api<DeviceStart>("/github/oauth/device/start", { method: "POST", json: { client_id: clientId, scope } }),
    onSuccess: (f) => { setOutcome(""); setFlow(f); poll(f, f.interval); },
  });
  const saveToken = useMutation({
    mutationFn: () => api<GithubConn>("/github/connection", { method: "PUT", json: { token, api_base: apiBase } }),
    onSuccess: () => { setToken(""); finished(); },
  });
  const c = conn.data;
  const check = useCheck("/github/check", () => ({ token, api_base: apiBase }));

  return (
    <div className="space-y-4">
      <form onSubmit={(e) => { e.preventDefault(); saveToken.mutate(); }} className="space-y-3">
        <h3 className="text-sm font-semibold">Personal access token</h3>
        <label className="block"><Label>Personal access token</Label><Input required type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="github_pat_…" />
          <span className="mt-1 block text-xs text-muted">Fine-grained token with read access to Contents, Pull requests, Issues and Metadata. Stored encrypted, never shown again.</span></label>
        <label className="block"><Label>API URL (change for GitHub Enterprise)</Label><Input value={apiBase} onChange={(e) => setApiBase(e.target.value)} /></label>
        <ErrorNote error={saveToken.error} />
        {check.message}
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={token.length < 8 || check.pending} onClick={check.run}>Test connection</Button><Button disabled={saveToken.isPending}>Save token</Button></div>
      </form>
      <div className="border-t border-line pt-3">
        <button type="button" className="text-sm text-brand hover:underline" onClick={() => setShowToken((v) => !v)} aria-expanded={!showToken}>{showToken ? "Or sign in with GitHub (device flow)" : "Hide device flow"}</button>
        {!showToken && (flow ? (
          <div className="mt-3 rounded-lg border border-line bg-slate-50 p-4" role="status">
            <p className="text-sm">1. Open <a className="inline-flex items-center gap-1 font-medium text-brand hover:underline" href={safeHref(flow.verification_uri)} target="_blank" rel="noreferrer">{flow.verification_uri}<ExternalLink className="h-3 w-3" /></a></p>
            <p className="mt-2 text-sm">2. Enter this code:</p>
            <div className="my-2 flex items-center gap-2">
              <code className="rounded bg-white px-3 py-2 text-xl font-semibold tracking-widest" aria-label="Device code">{flow.user_code}</code>
              <Button size="sm" variant="outline" type="button" onClick={() => navigator.clipboard?.writeText(flow.user_code)}><Copy className="h-3.5 w-3.5" />Copy</Button>
            </div>
            <p className="text-xs text-muted">Waiting for approval… this updates automatically.</p>
            <Button size="sm" variant="ghost" className="mt-2" onClick={() => { clearTimeout(timer.current); setFlow(null); }}>Cancel</Button>
          </div>
        ) : (
          <div className="mt-3 space-y-3">
            <label className="block"><Label>OAuth App Client ID <span className="text-muted">(Device Flow enabled)</span></Label>
              <Input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="Iv1.0123456789abcdef" /></label>
            <label className="block"><Label>Access</Label>
              <select className={selectCls} value={scope} onChange={(e) => setScope(e.target.value)}>
                {SCOPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <Button type="button" disabled={start.isPending || !clientId} onClick={() => start.mutate()}>{c?.connected ? "Reconnect with GitHub" : "Connect with GitHub"}</Button>
            <p className="text-xs text-muted">Register an OAuth App at github.com/settings/developers, tick “Enable Device Flow”, and paste its Client ID. No client secret is needed.</p>
          </div>))}
        <ErrorNote error={start.error} />
        {outcome && <p role="alert" className="mt-2 text-sm text-red-600">{outcome}</p>}
      </div>
    </div>
  );
}
