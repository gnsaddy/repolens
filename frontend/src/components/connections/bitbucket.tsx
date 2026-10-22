import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { selectCls } from "@/components/ConnectionCard";
import { useToast } from "@/components/feedback";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type BitbucketConn } from "@/lib/api";
import type { Integration } from "./types";

export function useBitbucket(): Integration {
  const qc = useQueryClient();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["bitbucket-conn"], queryFn: () => api<BitbucketConn>("/bitbucket/connection") });
  const remove = useMutation({ mutationFn: () => api("/bitbucket/connection", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["bitbucket-conn"] }); toast("Bitbucket disconnected"); } });
  const c = conn.data;
  return {
    connected: !!c?.connected, loading: conn.isLoading,
    detail: c?.connected ? (c.deployment === "server" ? `Server · ${c.base_url}` : `Cloud · ${c.username ? (c.username.includes("@") ? `API token (${c.username})` : `app password (${c.username})`) : "access token"}`) : "",
    test: async () => { const r = await api<{ ok: boolean; user?: string; error?: string }>("/bitbucket/test", { method: "POST" }); return r.ok ? `Connected as ${r.user}` : { error: r.error ?? "Failed" }; },
    disconnect: () => remove.mutateAsync(),
    disconnectMessage: "The stored credential is deleted.",
  };
}

const CLOUD_KINDS = [
  ["api", "Atlassian API token (your email + token)"],
  ["access", "Repository or workspace access token"],
  ["app", "App password (username + password)"],
] as const;
type Kind = (typeof CLOUD_KINDS)[number][0];

export function BitbucketForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const existing = useQuery({ queryKey: ["bitbucket-conn"], queryFn: () => api<BitbucketConn>("/bitbucket/connection") }).data;
  const [deployment, setDeployment] = useState<"cloud" | "server">(existing?.deployment ?? "cloud");
  const [kind, setKind] = useState<Kind>(existing?.username ? (existing.username.includes("@") ? "api" : "app") : existing?.connected ? "access" : "api");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "");
  const [username, setUsername] = useState(existing?.username ?? "");
  const [token, setToken] = useState("");
  const needsUser = deployment === "cloud" && kind !== "access";
  const save = useMutation({
    mutationFn: () => api("/bitbucket/connection", { method: "PUT", json: { deployment, base_url: deployment === "server" ? baseUrl : "", username: needsUser ? username : "", token } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["bitbucket-conn"] }); toast("Bitbucket connected"); onDone(); },
  });
  const help = deployment === "server" ? "Profile → Manage account → Personal access tokens, with Project read and Repository read."
    : kind === "api" ? "Create it at id.atlassian.com → Security → API tokens → Create API token with scopes → Bitbucket. Needs read:repository:bitbucket and read:pullrequest:bitbucket. Use your Atlassian email as the username."
    : kind === "access" ? "Repository settings → Security → Access tokens (or Workspace settings). Read access to repositories and pull requests. No username."
    : "Personal settings → App passwords, with Repositories: read and Pull requests: read. Use your Bitbucket username.";
  return (
    <form className="grid gap-3" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <div><Label>Deployment</Label><select aria-label="Bitbucket deployment" className={selectCls} value={deployment} onChange={(e) => setDeployment(e.target.value as "cloud" | "server")}><option value="cloud">Bitbucket Cloud</option><option value="server">Server / Data Center</option></select></div>
      {deployment === "cloud" && <div><Label>Credential type</Label><select aria-label="Bitbucket credential type" className={selectCls} value={kind} onChange={(e) => setKind(e.target.value as Kind)}>{CLOUD_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>}
      {deployment === "server" && <div><Label>Base URL</Label><Input aria-label="Bitbucket base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://bitbucket.company.com" /></div>}
      {needsUser && <div><Label>{kind === "api" ? "Atlassian email" : "Bitbucket username"}</Label><Input aria-label="Bitbucket username" required value={username} onChange={(e) => setUsername(e.target.value)} placeholder={kind === "api" ? "you@company.com" : "your-username"} /></div>}
      <div><Label>{deployment === "server" ? "Personal access token" : kind === "app" ? "App password" : "Token"}</Label><Input aria-label="Bitbucket token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        <p className="mt-1 text-xs text-muted">{help} Use a read-only credential. Stored encrypted, never shown again.</p></div>
      <ErrorNote error={save.error} />
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onDone}>Cancel</Button><Button disabled={save.isPending || token.length < 4 || (deployment === "server" && !baseUrl) || (needsUser && !username)}>Save connection</Button></div>
    </form>
  );
}
