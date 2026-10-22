import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { selectCls } from "@/components/ConnectionCard";
import { useToast } from "@/components/feedback";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type ConfluenceConn } from "@/lib/api";
import type { Integration } from "./types";

export function useConfluence(): Integration {
  const qc = useQueryClient();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["confluence-conn"], queryFn: () => api<ConfluenceConn>("/wiki/confluence/connection") });
  const remove = useMutation({ mutationFn: () => api("/wiki/confluence/connection", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["confluence-conn"] }); toast("Confluence disconnected"); } });
  const c = conn.data;
  return {
    connected: !!c?.connected, loading: conn.isLoading,
    detail: c?.connected ? `${c.base_url} · ${c.deployment === "cloud" ? c.email : "Data Center"}` : "",
    test: async () => { const r = await api<{ ok: boolean; user?: string; error?: string }>("/wiki/confluence/test", { method: "POST" }); return r.ok ? `Authenticated as ${r.user}` : { error: r.error ?? "Failed" }; },
    disconnect: () => remove.mutateAsync(),
    disconnectMessage: "The stored token is deleted. Pages already imported stay in the wiki but stop syncing.",
  };
}

export function ConfluenceForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const existing = useQuery({ queryKey: ["confluence-conn"], queryFn: () => api<ConfluenceConn>("/wiki/confluence/connection") }).data;
  const [f, setF] = useState({ base_url: existing?.base_url ?? "", deployment: existing?.deployment ?? "cloud", email: existing?.email ?? "", token: "" });
  const save = useMutation({ mutationFn: () => api("/wiki/confluence/connection", { method: "PUT", json: f }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["confluence-conn"] }); toast("Confluence connected"); onDone(); } });
  return (
    <form className="grid gap-3" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <div><Label>Base URL</Label><Input aria-label="Confluence base URL" required value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://yourco.atlassian.net/wiki" /></div>
      <div><Label>Deployment</Label><select aria-label="Confluence deployment" className={selectCls} value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value as "cloud" | "datacenter" })}><option value="cloud">Cloud (email + API token)</option><option value="datacenter">Data Center (personal access token)</option></select></div>
      {f.deployment === "cloud" && <div><Label>Account email</Label><Input aria-label="Confluence account email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>}
      <div><Label>{f.deployment === "cloud" ? "API token" : "Personal access token"}</Label><Input aria-label="Confluence token" type="password" autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} />
        <p className="mt-1 text-xs text-muted">Stored encrypted and never shown again. Use a read-only account.</p></div>
      <ErrorNote error={save.error} />
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onDone}>Cancel</Button><Button disabled={save.isPending || f.base_url.length < 8 || f.token.length < 4}>Save connection</Button></div>
    </form>
  );
}
