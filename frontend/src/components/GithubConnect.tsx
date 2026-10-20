import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ExternalLink, GitPullRequest } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, CardTitle, ErrorNote, Input, Label } from "@/components/ui";
import { api, type DevicePoll, type DeviceStart, type GithubConn } from "@/lib/api";

const SCOPES = [
  ["repo read:user", "Private + public repositories"],
  ["public_repo read:user", "Public repositories only"],
] as const;

/** GitHub sign-in via the OAuth device flow (no redirect URI, no client secret, token never reaches the browser). */
export default function GithubConnect({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const conn = useQuery({ queryKey: ["github-conn"], queryFn: () => api<GithubConn>("/github/connection") });
  const [clientId, setClientId] = useState("");
  const [scope, setScope] = useState<string>(SCOPES[0][0]);
  const [flow, setFlow] = useState<DeviceStart | null>(null);
  const [outcome, setOutcome] = useState<string>("");
  const [showToken, setShowToken] = useState(false);
  const [token, setToken] = useState("");
  const [apiBase, setApiBase] = useState("https://api.github.com");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => { if (conn.data?.oauth_client_id && !clientId) setClientId(conn.data.oauth_client_id); }, [conn.data, clientId]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const refresh = () => { qc.invalidateQueries({ queryKey: ["github-conn"] }); qc.invalidateQueries({ queryKey: ["llm-settings"] }); };
  const poll = (f: DeviceStart, interval: number) => {
    timer.current = setTimeout(async () => {
      try {
        const r = await api<DevicePoll>("/github/oauth/device/poll", { method: "POST", json: { flow_id: f.flow_id } });
        if (r.status === "pending") return poll(f, r.interval ?? interval);
        setFlow(null);
        setOutcome(r.status === "success" ? "" : r.status === "denied" ? "Authorisation was denied on GitHub." : r.status === "expired" ? "The code expired — start again." : r.error ?? "GitHub returned an error.");
        if (r.status === "success") refresh();
      } catch (e) { setFlow(null); setOutcome((e as Error).message); }
    }, interval * 1000);
  };
  const start = useMutation({
    mutationFn: () => api<DeviceStart>("/github/oauth/device/start", { method: "POST", json: { client_id: clientId, scope } }),
    onSuccess: (f) => { setOutcome(""); setFlow(f); poll(f, f.interval); },
  });
  const saveToken = useMutation({
    mutationFn: () => api<GithubConn>("/github/connection", { method: "PUT", json: { token, api_base: apiBase } }),
    onSuccess: () => { setToken(""); refresh(); },
  });
  const remove = useMutation({ mutationFn: () => api("/github/connection", { method: "DELETE" }), onSuccess: refresh });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; user?: string; error?: string }>("/github/test", { method: "POST" }) });
  const c = conn.data;

  return (
    <Card className="lg:col-start-1">
      <CardTitle right={c?.connected && <div className="flex gap-1"><Badge tone="green">connected</Badge><Badge tone="brand">{c.auth_method === "oauth" ? `OAuth${c.login ? ` · @${c.login}` : ""}` : "token"}</Badge></div>}>
        <span className="flex items-center gap-2"><GitPullRequest className="h-4 w-4" />GitHub</span>
      </CardTitle>
      <p className="mb-3 text-xs text-muted">Ingest pull requests into the knowledge graph and optionally use GitHub Models. The token is stored encrypted and never shown again.</p>
      {!admin ? <p className="text-sm text-muted">{c?.connected ? "GitHub is connected." : "An admin must connect GitHub."}</p> : (<>
        {flow ? (
          <div className="rounded-lg border border-line bg-slate-50 p-4" role="status">
            <p className="text-sm">1. Open <a className="inline-flex items-center gap-1 font-medium text-brand hover:underline" href={flow.verification_uri} target="_blank" rel="noreferrer">{flow.verification_uri}<ExternalLink className="h-3 w-3" /></a></p>
            <p className="mt-2 text-sm">2. Enter this code:</p>
            <div className="my-2 flex items-center gap-2">
              <code className="rounded bg-white px-3 py-2 text-xl font-semibold tracking-widest" aria-label="Device code">{flow.user_code}</code>
              <Button size="sm" variant="outline" type="button" onClick={() => navigator.clipboard?.writeText(flow.user_code)}><Copy className="h-3.5 w-3.5" />Copy</Button>
            </div>
            <p className="text-xs text-muted">Waiting for approval… this page updates automatically.</p>
            <Button size="sm" variant="ghost" className="mt-2" onClick={() => { clearTimeout(timer.current); setFlow(null); }}>Cancel</Button>
          </div>
        ) : (
          <div className="space-y-3">
            <label className="block"><Label>OAuth App Client ID <span className="text-muted">(Device Flow enabled)</span></Label>
              <Input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="Iv1.0123456789abcdef" /></label>
            <label className="block"><Label>Access</Label>
              <select className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={scope} onChange={(e) => setScope(e.target.value)}>
                {SCOPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <div className="flex flex-wrap gap-2">
              <Button type="button" disabled={start.isPending || !clientId} onClick={() => start.mutate()}>{c?.connected ? "Reconnect with GitHub" : "Connect with GitHub"}</Button>
              {c?.connected && <Button type="button" variant="outline" disabled={test.isPending} onClick={() => test.mutate()}>Test</Button>}
              {c?.connected && <Button type="button" variant="ghost" onClick={() => { if (confirm("Remove the stored GitHub token? Revoke the app at github.com/settings/applications too.")) remove.mutate(); }}>Disconnect</Button>}
            </div>
            <p className="text-xs text-muted">Register an OAuth App at github.com/settings/developers, tick “Enable Device Flow”, and paste its Client ID. No client secret is needed.</p>
          </div>)}
        <ErrorNote error={start.error ?? remove.error} />
        {outcome && <p role="alert" className="mt-2 text-sm text-red-600">{outcome}</p>}
        {test.data && <p role="status" className={`mt-2 text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? `Connected as ${test.data.user}` : test.data.error}</p>}
        <div className="mt-4 border-t border-line pt-3">
          <button type="button" className="text-xs text-brand hover:underline" onClick={() => setShowToken((v) => !v)} aria-expanded={showToken}>{showToken ? "Hide" : "Use a personal access token instead"}</button>
          {showToken && (
            <form onSubmit={(e) => { e.preventDefault(); saveToken.mutate(); }} className="mt-3 space-y-3">
              <label className="block"><Label>API URL (change for GitHub Enterprise)</Label><Input value={apiBase} onChange={(e) => setApiBase(e.target.value)} /></label>
              <label className="block"><Label>Personal access token</Label><Input required type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="github_pat_…" /></label>
              <ErrorNote error={saveToken.error} /><Button disabled={saveToken.isPending}>Save token</Button>
            </form>)}
        </div>
      </>)}
    </Card>
  );
}
