import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useConfirm } from "@/components/feedback";
import { RefreshCw, Trash2, Upload } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Label, Spinner, Textarea, statusTone } from "@/components/ui";
import { api, type ConfluenceConn, type ConfluencePreview, type WikiOverview, type WikiSourceStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";
import Collections from "./Collections";

const DOC_RE = /\.(md|mdx|rst|txt|adoc)$/i;

export default function Sources({ overview }: { overview?: WikiOverview }) {
  const ask = useConfirm();
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const refresh = () => { qc.invalidateQueries({ queryKey: ["wiki-overview"] }); qc.invalidateQueries({ queryKey: ["wiki-sources"] }); qc.invalidateQueries({ queryKey: ["wiki-tree"] }); qc.invalidateQueries({ queryKey: ["wiki-page"] }); };
  const sources = useQuery({ queryKey: ["wiki-sources"], queryFn: () => api<WikiSourceStatus[]>("/wiki/sources"), refetchInterval: (q) => (q.state.data?.some((s) => s.status === "syncing" || s.repo_status === "indexing") ? 3000 : false) });
  const sync = useMutation({ mutationFn: (id: string) => api(`/wiki/sources/${id}/sync`, { method: "POST" }), onSuccess: refresh });
  const del = useMutation({ mutationFn: (id: string) => api(`/wiki/sources/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const collections = (overview?.sources ?? []).filter((s) => s.kind === "collection");

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>Connected sources</CardTitle>
        <ErrorNote error={sync.error || del.error} />
        {sources.isLoading ? <Spinner /> : !sources.data?.length ? <Empty>No Confluence sources yet. Connect Confluence below, or add documents and URLs.</Empty> : (
          <ul className="divide-y divide-line">{sources.data.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
              <div className="min-w-0 flex-1"><div className="font-medium">{s.name}</div>
                <div className="truncate text-xs text-muted">{s.url} · {s.mode}{s.space_key ? ` · ${s.space_key}` : ""} · {s.pages} pages · {s.last_synced_at ? `synced ${timeAgo(s.last_synced_at)}` : "never synced"}</div>
                {s.error && <div role="alert" className="text-xs text-red-600">{s.error}</div>}</div>
              <Badge tone={statusTone(s.status)}>{s.status}</Badge><Badge tone={s.visibility === "restricted" ? "amber" : "gray"}>{s.visibility}</Badge>
              {admin && <>
                <Button variant="outline" size="sm" aria-label={`Sync ${s.name}`} disabled={sync.isPending} onClick={() => sync.mutate(s.id)}><RefreshCw className="h-3.5 w-3.5" />Sync</Button>
                <Button variant="ghost" size="sm" aria-label={`Remove ${s.name}`} onClick={async () => { if (await ask({ title: `Remove "${s.name}"?`, message: "All pages imported from this source are deleted from the wiki. The original Confluence content is not touched.", confirmLabel: "Remove source", danger: true })) del.mutate(s.id); }}><Trash2 className="h-3.5 w-3.5" /></Button></>}
            </li>))}</ul>)}
      </Card>
      <Collections sources={overview?.sources ?? []} onChanged={refresh} />
      <div className="grid gap-4 lg:grid-cols-2">
        <AddDocs collections={collections.map((c) => ({ id: c.repo_id, name: c.name }))} onDone={refresh} />
        <ImportUrl collections={collections.map((c) => ({ id: c.repo_id, name: c.name }))} onDone={refresh} />
      </div>
      {admin ? <ConfluenceForm onDone={refresh} /> : <Card><CardTitle>Confluence</CardTitle><p className="text-sm text-muted">Only admins can connect Confluence and import spaces.</p></Card>}
    </div>
  );
}

function CollectionPick({ collections, value, onChange }: { collections: { id: string; name: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <select aria-label="Collection" className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">New collection…</option>{collections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
  );
}

function AddDocs({ collections, onDone }: { collections: { id: string; name: string }[]; onDone: () => void }) {
  const [coll, setColl] = useState("");
  const [name, setName] = useState("");
  const [path, setPath] = useState("notes.md");
  const [text, setText] = useState("");
  const [files, setFiles] = useState<{ path: string; content: string }[]>([]);
  const save = useMutation({
    mutationFn: async () => {
      let id = coll;
      if (!id) id = (await api<{ id: string }>("/knowledge/collections", { method: "POST", json: { name: name.trim() } })).id;
      const documents = [...files, ...(text.trim() ? [{ path, content: text }] : [])];
      return api(`/knowledge/collections/${id}/documents`, { method: "POST", json: { documents } });
    },
    onSuccess: () => { setText(""); setFiles([]); onDone(); },
  });
  const pick = async (list: FileList | null) => {
    const out: { path: string; content: string }[] = [];
    for (const f of Array.from(list ?? [])) if (DOC_RE.test(f.name)) out.push({ path: f.name, content: await f.text() });
    setFiles(out);
  };
  const ready = (coll || /^[\w.\- ]+$/.test(name.trim())) && (files.length > 0 || (text.trim() && DOC_RE.test(path)));
  return (
    <Card>
      <CardTitle>Add README / text files</CardTitle>
      <form className="space-y-3" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        <CollectionPick collections={collections} value={coll} onChange={setColl} />
        {!coll && <div><Label>New collection name</Label><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="team-handbook" aria-label="Collection name" /></div>}
        <div><Label>Upload .md / .txt / .rst files</Label><input type="file" multiple accept=".md,.mdx,.rst,.txt,.adoc" aria-label="Files" onChange={(e) => pick(e.target.files)} className="block text-sm" />
          {!!files.length && <p className="mt-1 text-xs text-muted">{files.length} file(s) ready</p>}</div>
        <div><Label>…or paste text</Label><Input value={path} onChange={(e) => setPath(e.target.value)} aria-label="File name" className="mb-2" />
          <Textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="# Title&#10;Paste a README or notes…" aria-label="Pasted text" /></div>
        <ErrorNote error={save.error} />
        {save.isSuccess && <p role="status" className="text-sm text-emerald-700">Added — indexing in the background.</p>}
        <Button disabled={!ready || save.isPending}><Upload className="h-4 w-4" />Add to wiki</Button>
      </form>
    </Card>
  );
}

function ImportUrl({ collections, onDone }: { collections: { id: string; name: string }[]; onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [coll, setColl] = useState("");
  const [name, setName] = useState("");
  const imp = useMutation({
    mutationFn: () => api("/wiki/import-url", { method: "POST", json: { url, ...(coll ? { collection_id: coll } : { name: name.trim() }) } }),
    onSuccess: () => { setUrl(""); onDone(); },
  });
  return (
    <Card>
      <CardTitle>Import from a web URL</CardTitle>
      <form className="space-y-3" onSubmit={(e: FormEvent) => { e.preventDefault(); imp.mutate(); }}>
        <p className="text-xs text-muted">Public https pages only: raw README, GitHub file links, plain text or HTML. For anything behind a login use Confluence.</p>
        <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/org/repo/blob/main/README.md" aria-label="Page URL" />
        <CollectionPick collections={collections} value={coll} onChange={setColl} />
        {!coll && <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Collection name (optional)" aria-label="New collection name" />}
        <ErrorNote error={imp.error} />
        {imp.isSuccess && <p role="status" className="text-sm text-emerald-700">Imported — indexing in the background.</p>}
        <Button disabled={!/^https:\/\//.test(url) || imp.isPending}>Import</Button>
      </form>
    </Card>
  );
}

function ConfluenceForm({ onDone }: { onDone: () => void }) {
  const ask = useConfirm();
  const qc = useQueryClient();
  const conn = useQuery({ queryKey: ["confluence-conn"], queryFn: () => api<ConfluenceConn>("/wiki/confluence/connection") });
  const [f, setF] = useState({ base_url: "", deployment: "cloud", email: "", token: "" });
  const [edit, setEdit] = useState(false);
  const save = useMutation({
    mutationFn: () => api("/wiki/confluence/connection", { method: "PUT", json: f }),
    onSuccess: () => { setF({ ...f, token: "" }); setEdit(false); qc.invalidateQueries({ queryKey: ["confluence-conn"] }); },
  });
  const remove = useMutation({ mutationFn: () => api("/wiki/confluence/connection", { method: "DELETE" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["confluence-conn"] }) });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; user?: string; error?: string }>("/wiki/confluence/test", { method: "POST" }) });
  const c = conn.data;
  const showForm = edit || (c && !c.connected);
  return (
    <Card>
      <CardTitle right={c?.connected ? <Badge tone="green">connected</Badge> : <Badge>not connected</Badge>}>Confluence</CardTitle>
      {conn.isLoading ? <Spinner /> : showForm ? (
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
          <div className="sm:col-span-2"><Label>Base URL</Label><Input value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://yourco.atlassian.net/wiki" aria-label="Base URL" /></div>
          <div><Label>Deployment</Label><select aria-label="Deployment" className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value })}><option value="cloud">Cloud (email + API token)</option><option value="datacenter">Data Center (personal access token)</option></select></div>
          {f.deployment === "cloud" && <div><Label>Email</Label><Input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} aria-label="Email" /></div>}
          <div className="sm:col-span-2"><Label>{f.deployment === "cloud" ? "API token" : "Personal access token"}</Label><Input type="password" autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} aria-label="Token" />
            <p className="mt-1 text-xs text-muted">Stored encrypted and never shown again. Use a read-only account.</p></div>
          <div className="sm:col-span-2"><ErrorNote error={save.error} /></div>
          <div className="flex gap-2 sm:col-span-2"><Button disabled={save.isPending || f.base_url.length < 8 || f.token.length < 4}>Save connection</Button>{c?.connected && <Button type="button" variant="outline" onClick={() => setEdit(false)}>Cancel</Button>}</div>
        </form>
      ) : c?.connected && (<>
        <p className="mb-3 text-sm">{c.base_url} <span className="text-muted">· {c.deployment}{c.email ? ` · ${c.email}` : ""}</span></p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => test.mutate()} disabled={test.isPending}>Test connection</Button>
          <Button variant="outline" size="sm" onClick={() => { setF({ base_url: c.base_url ?? "", deployment: c.deployment ?? "cloud", email: c.email ?? "", token: "" }); setEdit(true); }}>Change</Button>
          <Button variant="ghost" size="sm" onClick={async () => { if (await ask({ title: "Disconnect Confluence?", message: "The stored token is deleted. Pages already imported stay in the wiki, but they stop syncing.", confirmLabel: "Disconnect", danger: true })) remove.mutate(); }}>Disconnect</Button>
          {test.data && <span role="status" className={`text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? `Authenticated as ${test.data.user}` : test.data.error}</span>}
          <ErrorNote error={test.error} /></div>
        <ConfluenceImport onDone={onDone} />
      </>)}
    </Card>
  );
}

function ConfluenceImport({ onDone }: { onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState("auto");
  const [maxPages, setMax] = useState(300);
  const [vis, setVis] = useState("restricted");
  const preview = useMutation({ mutationFn: () => api<ConfluencePreview>("/wiki/confluence/preview", { method: "POST", json: { url } }) });
  const imp = useMutation({ mutationFn: () => api("/wiki/confluence/import", { method: "POST", json: { url, mode, max_pages: maxPages, visibility: vis } }), onSuccess: () => { preview.reset(); onDone(); } });
  const pv = preview.data;
  return (
    <div className="mt-4 space-y-3 border-t border-line pt-4">
      <h4 className="text-sm font-semibold">Import pages</h4>
      <Input value={url} onChange={(e) => { setUrl(e.target.value); preview.reset(); }} placeholder="https://yourco.atlassian.net/wiki/spaces/ENG/pages/12345/Handbook" aria-label="Confluence URL" />
      <div className="flex flex-wrap items-end gap-3">
        <div><Label>Scope</Label><select aria-label="Scope" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={mode} onChange={(e) => setMode(e.target.value)}><option value="auto">Auto</option><option value="page">This page only</option><option value="tree">Page and children</option><option value="space">Whole space</option></select></div>
        <div><Label>Max pages</Label><Input type="number" min={1} max={2000} value={maxPages} onChange={(e) => setMax(+e.target.value)} className="w-24" aria-label="Max pages" /></div>
        <div><Label>Who can see it</Label><select aria-label="Visibility" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={vis} onChange={(e) => setVis(e.target.value)}><option value="restricted">Restricted (grant access per source)</option><option value="org">Whole organization</option></select></div>
      </div>
      <ErrorNote error={preview.error || imp.error} />
      {pv && <p role="status" className="text-sm">Found {pv.kind} <b>{pv.title || pv.space_key}</b> in space {pv.space_key}. Suggested scope: {pv.suggested_mode}.</p>}
      {imp.isSuccess && <p role="status" className="text-sm text-emerald-700">Import started — pages appear as they are indexed.</p>}
      <div className="flex gap-2"><Button variant="outline" onClick={() => preview.mutate()} disabled={url.length < 8 || preview.isPending}>Preview</Button><Button onClick={() => imp.mutate()} disabled={url.length < 8 || imp.isPending}>Import</Button></div>
    </div>
  );
}
