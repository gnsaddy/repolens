import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { selectCls } from "@/components/ConnectionCard";
import { useToast } from "@/components/feedback";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type JiraConnection } from "@/lib/api";
import type { Integration } from "./types";
import { useCheck } from "./useCheck";

export function useJira(): Integration {
  const qc = useQueryClient();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["jira-conn"], queryFn: () => api<JiraConnection | null>("/jira/connection") });
  const remove = useMutation({ mutationFn: () => api("/jira/connection", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["jira-conn"] }); toast("Jira disconnected"); } });
  const c = conn.data;
  return {
    connected: !!c, loading: conn.isLoading, account: c?.account || undefined,
    detail: c ? `${c.base_url} · ${c.deployment === "cloud" ? c.email : "Data Center"}${c.allow_publish ? " · can post comments" : ""}` : "",
    test: async () => { const r = await api<{ ok: boolean; user?: string; error?: string }>("/jira/test", { method: "POST" }); if (r.ok) qc.invalidateQueries({ queryKey: ["jira-conn"] }); return r.ok ? `Connected as ${r.user}` : { error: r.error ?? "Failed" }; },
    disconnect: () => remove.mutateAsync(),
    disconnectMessage: "The stored token is deleted. Tickets already imported stay in the wiki but stop syncing.",
  };
}

export function JiraForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const existing = useQuery({ queryKey: ["jira-conn"], queryFn: () => api<JiraConnection | null>("/jira/connection") }).data;
  const [f, setF] = useState({ base_url: existing?.base_url ?? "", deployment: existing?.deployment ?? "cloud", email: existing?.email ?? "", token: "", allow_publish: existing?.allow_publish ?? false });
  const save = useMutation({ mutationFn: () => api("/jira/connection", { method: "PUT", json: f }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["jira-conn"] }); toast("Jira connected"); onDone(); } });
  const check = useCheck("/jira/check", () => f);
  const ready = f.base_url.length > 8 && f.token.length > 3 && (f.deployment !== "cloud" || f.email.includes("@"));
  return (
    <form className="grid gap-3" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <div><Label>Jira URL</Label><Input aria-label="Jira URL" required value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://acme.atlassian.net" /></div>
      <div><Label>Deployment</Label><select aria-label="Jira deployment" className={selectCls} value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value as "cloud" | "datacenter" })}><option value="cloud">Jira Cloud (email + API token)</option><option value="datacenter">Data Center (personal access token)</option></select></div>
      {f.deployment === "cloud" && <div><Label>Account email</Label><Input aria-label="Jira account email" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>}
      <div><Label>{f.deployment === "cloud" ? "API token" : "Personal access token"}</Label><Input aria-label="Jira token" type="password" required autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} placeholder="Stored encrypted, never shown again" /></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.allow_publish} onChange={(e) => setF({ ...f, allow_publish: e.target.checked })} />Allow posting RCA comments to issues (always after your approval)</label>
      <ErrorNote error={save.error} />
      {check.message}
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onDone}>Cancel</Button><Button type="button" variant="outline" disabled={!ready || check.pending} onClick={check.run}>Test connection</Button><Button disabled={save.isPending}>Save connection</Button></div>
    </form>
  );
}
