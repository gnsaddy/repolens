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
    detail: c?.connected ? (c.deployment === "server" ? `Server · ${c.base_url}` : `Cloud · ${c.username ? `app password (${c.username})` : "access token"}`) : "",
    test: async () => { const r = await api<{ ok: boolean; user?: string; error?: string }>("/bitbucket/test", { method: "POST" }); return r.ok ? `Connected as ${r.user}` : { error: r.error ?? "Failed" }; },
    disconnect: () => remove.mutateAsync(),
    disconnectMessage: "The stored credential is deleted.",
  };
}

export function BitbucketForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const existing = useQuery({ queryKey: ["bitbucket-conn"], queryFn: () => api<BitbucketConn>("/bitbucket/connection") }).data;
  const [f, setF] = useState({ deployment: existing?.deployment ?? "cloud", base_url: existing?.base_url ?? "", username: existing?.username ?? "", token: "" });
  const save = useMutation({ mutationFn: () => api("/bitbucket/connection", { method: "PUT", json: f }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["bitbucket-conn"] }); toast("Bitbucket connected"); onDone(); } });
  return (
    <form className="grid gap-3" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <div><Label>Deployment</Label><select aria-label="Bitbucket deployment" className={selectCls} value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value as "cloud" | "server" })}><option value="cloud">Bitbucket Cloud</option><option value="server">Server / Data Center</option></select></div>
      {f.deployment === "server"
        ? <div><Label>Base URL</Label><Input aria-label="Bitbucket base URL" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://bitbucket.company.com" /></div>
        : <div><Label>Username (app password only)</Label><Input aria-label="Bitbucket username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} placeholder="leave empty for an access token" /></div>}
      <div><Label>{f.deployment === "server" ? "Personal access token" : f.username ? "App password" : "Access token"}</Label><Input aria-label="Bitbucket token" type="password" autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} />
        <p className="mt-1 text-xs text-muted">{f.deployment === "server" ? "Profile → Personal access tokens, with Project read and Repository read." : "An app password (Account settings → App passwords) with Repositories: read and Pull requests: read, or a repository / workspace access token. Use a read-only credential."}</p></div>
      <ErrorNote error={save.error} />
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onDone}>Cancel</Button><Button disabled={save.isPending || f.token.length < 4 || (f.deployment === "server" && !f.base_url)}>Save connection</Button></div>
    </form>
  );
}
