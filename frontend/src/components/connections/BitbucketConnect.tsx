import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitMerge } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ConnectionCard, StatusPill, selectCls } from "@/components/ConnectionCard";
import { useConfirm, useToast } from "@/components/feedback";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, type BitbucketConn } from "@/lib/api";

export default function BitbucketConnect({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const ask = useConfirm();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["bitbucket-conn"], queryFn: () => api<BitbucketConn>("/bitbucket/connection") });
  const [f, setF] = useState({ deployment: "cloud", base_url: "", username: "", token: "" });
  const [edit, setEdit] = useState(false);
  const save = useMutation({ mutationFn: () => api("/bitbucket/connection", { method: "PUT", json: f }), onSuccess: () => { setF({ ...f, token: "" }); setEdit(false); qc.invalidateQueries({ queryKey: ["bitbucket-conn"] }); toast("Bitbucket connected"); } });
  const remove = useMutation({ mutationFn: () => api("/bitbucket/connection", { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["bitbucket-conn"] }); toast("Bitbucket disconnected"); } });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; user?: string; error?: string }>("/bitbucket/test", { method: "POST" }) });
  const c = conn.data;
  const showForm = admin && (edit || !c?.connected);
  return (
    <ConnectionCard icon={GitMerge} tint="blue" title="Bitbucket" status={<StatusPill on={!!c?.connected} />}
      description="Browse repositories and pull requests, and clone private repositories. Cloud, Server and Data Center are supported."
      footer={!showForm && c?.connected && admin ? (<>
        <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>Test connection</Button>
        <Button size="sm" variant="outline" onClick={() => { setF({ deployment: c.deployment ?? "cloud", base_url: c.base_url ?? "", username: c.username ?? "", token: "" }); setEdit(true); }}>Change</Button>
        <Button size="sm" variant="ghost" onClick={async () => { if (await ask({ title: "Disconnect Bitbucket?", message: "The stored credential is deleted.", confirmLabel: "Disconnect", danger: true })) remove.mutate(); }}>Disconnect</Button>
        {test.data && <span role="status" className={`text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? `Connected as ${test.data.user}` : test.data.error}</span>}</>) : undefined}>
      {showForm ? (
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
          <div><Label>Deployment</Label><select aria-label="Bitbucket deployment" className={selectCls} value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value })}><option value="cloud">Bitbucket Cloud</option><option value="server">Server / Data Center</option></select></div>
          {f.deployment === "server"
            ? <div><Label>Base URL</Label><Input aria-label="Bitbucket base URL" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://bitbucket.company.com" /></div>
            : <div><Label>Username (app password only)</Label><Input aria-label="Bitbucket username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} placeholder="leave empty for an access token" /></div>}
          <div className="sm:col-span-2"><Label>{f.deployment === "server" ? "Personal access token" : f.username ? "App password" : "Access token"}</Label><Input aria-label="Bitbucket token" type="password" autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} />
            <p className="mt-1 text-xs text-muted">{f.deployment === "server" ? "Profile → Personal access tokens, with Project read and Repository read." : "An app password (Account settings → App passwords) with Repositories: read and Pull requests: read, or a repository / workspace access token. Use a read-only credential."}</p></div>
          <div className="sm:col-span-2"><ErrorNote error={save.error} /></div>
          <div className="flex gap-2 sm:col-span-2"><Button disabled={save.isPending || f.token.length < 4 || (f.deployment === "server" && !f.base_url)}>Save connection</Button>{c?.connected && <Button type="button" variant="outline" onClick={() => setEdit(false)}>Cancel</Button>}</div>
        </form>
      ) : c?.connected ? (
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-muted">Type</dt><dd className="font-medium">{c.deployment === "server" ? "Server / Data Center" : "Cloud"}</dd></div>
          <div><dt className="text-xs text-muted">{c.deployment === "server" ? "Site" : "Credential"}</dt><dd className="truncate font-medium">{c.deployment === "server" ? c.base_url : c.username ? `app password · ${c.username}` : "access token"}</dd></div>
        </dl>
      ) : <p className="text-sm text-muted">Only admins can connect Bitbucket.</p>}
    </ConnectionCard>
  );
}
