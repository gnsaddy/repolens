import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ticket } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ConnectionCard, StatusPill, selectCls } from "@/components/ConnectionCard";
import { useConfirm, useToast } from "@/components/feedback";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type JiraConnection } from "@/lib/api";

export default function JiraConnect({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["jira-conn"], queryFn: () => api<JiraConnection | null>("/jira/connection") });
  const [f, setF] = useState({ base_url: "", deployment: "cloud", email: "", token: "", allow_publish: false });
  const [edit, setEdit] = useState(false);
  const c = conn.data;
  const save = useMutation({ mutationFn: () => api("/jira/connection", { method: "PUT", json: f }), onSuccess: () => { setF((x) => ({ ...x, token: "" })); setEdit(false); qc.invalidateQueries({ queryKey: ["jira-conn"] }); toast("Jira connected"); } });
  const remove = useMutation({ mutationFn: () => api("/jira/connection", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["jira-conn"] }); toast("Jira disconnected"); } });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; user?: string; error?: string }>("/jira/test", { method: "POST" }) });
  const showForm = admin && (edit || !c);
  return (
    <ConnectionCard icon={Ticket} tint="blue" title="Jira" status={<StatusPill on={!!c} />}
      description="Find bug tickets, investigate them, and post approved root-cause reports back as comments. Also feeds past tickets into search."
      footer={!showForm && c && admin ? (<>
        <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>Test connection</Button>
        <Button size="sm" variant="outline" onClick={() => { setF({ base_url: c.base_url, deployment: c.deployment, email: c.email, token: "", allow_publish: c.allow_publish }); setEdit(true); }}>Change</Button>
        <Button size="sm" variant="ghost" onClick={async () => { if (await ask({ title: "Disconnect Jira?", message: "The stored token is deleted. Imported tickets stay in the wiki but stop syncing.", confirmLabel: "Disconnect", danger: true })) remove.mutate(); }}>Disconnect</Button>
        {test.data && <span role="status" className={`text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? `Connected as ${test.data.user}` : test.data.error}</span>}</>) : undefined}>
      {showForm ? (
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
          <div className="sm:col-span-2"><Label>Jira URL</Label><Input aria-label="Jira URL" required value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://acme.atlassian.net" /></div>
          <div><Label>Deployment</Label><select aria-label="Jira deployment" className={selectCls} value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value })}><option value="cloud">Jira Cloud (email + API token)</option><option value="datacenter">Data Center (personal access token)</option></select></div>
          {f.deployment === "cloud" && <div><Label>Account email</Label><Input aria-label="Jira account email" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>}
          <div className="sm:col-span-2"><Label>{f.deployment === "cloud" ? "API token" : "Personal access token"}</Label><Input aria-label="Jira token" type="password" required autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} placeholder="Stored encrypted, never shown again" /></div>
          <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={f.allow_publish} onChange={(e) => setF({ ...f, allow_publish: e.target.checked })} />Allow posting RCA comments to issues (always after your approval)</label>
          <div className="sm:col-span-2"><ErrorNote error={save.error} /></div>
          <div className="flex gap-2 sm:col-span-2"><Button disabled={save.isPending}>Save connection</Button>{c && <Button type="button" variant="outline" onClick={() => setEdit(false)}>Cancel</Button>}</div>
        </form>
      ) : c ? (
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
          <div><dt className="text-xs text-muted">Site</dt><dd className="truncate font-medium">{c.base_url}</dd></div>
          <div><dt className="text-xs text-muted">Type</dt><dd className="font-medium">{c.deployment === "cloud" ? `Cloud · ${c.email}` : "Data Center"}</dd></div>
          <div><dt className="text-xs text-muted">Posting comments</dt><dd className="font-medium">{c.allow_publish ? "Allowed after approval" : "Off"}</dd></div>
        </dl>
      ) : <p className="text-sm text-muted">An admin needs to connect Jira.</p>}
    </ConnectionCard>
  );
}
