import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Library } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ConnectionCard, StatusPill, selectCls } from "@/components/ConnectionCard";
import { useConfirm, useToast } from "@/components/feedback";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type ConfluenceConn } from "@/lib/api";

export default function ConfluenceConnect({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["confluence-conn"], queryFn: () => api<ConfluenceConn>("/wiki/confluence/connection") });
  const [f, setF] = useState({ base_url: "", deployment: "cloud", email: "", token: "" });
  const [edit, setEdit] = useState(false);
  const c = conn.data;
  const on = !!c?.connected;
  const save = useMutation({ mutationFn: () => api("/wiki/confluence/connection", { method: "PUT", json: f }), onSuccess: () => { setF((x) => ({ ...x, token: "" })); setEdit(false); qc.invalidateQueries({ queryKey: ["confluence-conn"] }); toast("Confluence connected"); } });
  const remove = useMutation({ mutationFn: () => api("/wiki/confluence/connection", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["confluence-conn"] }); toast("Confluence disconnected"); } });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; user?: string; error?: string }>("/wiki/confluence/test", { method: "POST" }) });
  const showForm = admin && (edit || !on);
  return (
    <ConnectionCard icon={Library} tint="indigo" title="Confluence" status={<StatusPill on={on} />}
      description="Import spaces and pages into the DeepMind Wiki, kept up to date by version. Read-only: pages are never changed in Confluence."
      footer={!showForm && on && admin ? (<>
        <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>Test connection</Button>
        <Button size="sm" variant="outline" onClick={() => { setF({ base_url: c?.base_url ?? "", deployment: c?.deployment ?? "cloud", email: c?.email ?? "", token: "" }); setEdit(true); }}>Change</Button>
        <Button size="sm" variant="ghost" onClick={async () => { if (await ask({ title: "Disconnect Confluence?", message: "The stored token is deleted. Pages already imported stay in the wiki but stop syncing.", confirmLabel: "Disconnect", danger: true })) remove.mutate(); }}>Disconnect</Button>
        <Link to="/wiki?tab=sources" className="ml-auto text-sm text-brand hover:underline">Import pages →</Link>
        {test.data && <span role="status" className={`text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? `Authenticated as ${test.data.user}` : test.data.error}</span>}</>) : undefined}>
      {showForm ? (
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
          <div className="sm:col-span-2"><Label>Base URL</Label><Input aria-label="Confluence base URL" required value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://yourco.atlassian.net/wiki" /></div>
          <div><Label>Deployment</Label><select aria-label="Confluence deployment" className={selectCls} value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value })}><option value="cloud">Cloud (email + API token)</option><option value="datacenter">Data Center (personal access token)</option></select></div>
          {f.deployment === "cloud" && <div><Label>Account email</Label><Input aria-label="Confluence account email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>}
          <div className="sm:col-span-2"><Label>{f.deployment === "cloud" ? "API token" : "Personal access token"}</Label><Input aria-label="Confluence token" type="password" autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} />
            <p className="mt-1 text-xs text-muted">Stored encrypted and never shown again. Use a read-only account.</p></div>
          <div className="sm:col-span-2"><ErrorNote error={save.error} /></div>
          <div className="flex gap-2 sm:col-span-2"><Button disabled={save.isPending || f.base_url.length < 8 || f.token.length < 4}>Save connection</Button>{on && <Button type="button" variant="outline" onClick={() => setEdit(false)}>Cancel</Button>}</div>
        </form>
      ) : on ? (
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
          <div><dt className="text-xs text-muted">Site</dt><dd className="truncate font-medium">{c?.base_url}</dd></div>
          <div><dt className="text-xs text-muted">Type</dt><dd className="font-medium">{c?.deployment === "cloud" ? "Cloud" : "Data Center"}</dd></div>
          <div><dt className="text-xs text-muted">Account</dt><dd className="truncate font-medium">{c?.email || "personal access token"}</dd></div>
        </dl>
      ) : <p className="text-sm text-muted">An admin needs to connect Confluence.</p>}
    </ConnectionCard>
  );
}
