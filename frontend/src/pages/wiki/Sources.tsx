import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useConfirm, useToast } from "@/components/feedback";
import { RefreshCw, Trash2, Upload } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Label, Spinner, Textarea, statusTone } from "@/components/ui";
import { api, type ConnectorInfo, type ConfluenceConn, type ConfluencePreview, type WikiOverview, type WikiSourceStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";
import Collections from "./Collections";

export const SCHEDULES: [number, string][] = [[0, "Sync manually"], [1, "Hourly"], [6, "Every 6 hours"], [24, "Daily"], [168, "Weekly"]];
const DOC_RE = /\.(md|mdx|rst|txt|adoc)$/i;

export default function Sources({ overview }: { overview?: WikiOverview }) {
  const ask = useConfirm();
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const refresh = () => { qc.invalidateQueries({ queryKey: ["wiki-overview"] }); qc.invalidateQueries({ queryKey: ["wiki-sources"] }); qc.invalidateQueries({ queryKey: ["wiki-tree"] }); qc.invalidateQueries({ queryKey: ["wiki-page"] }); };
  const sources = useQuery({ queryKey: ["wiki-sources"], queryFn: () => api<WikiSourceStatus[]>("/wiki/sources"), refetchInterval: (q) => (q.state.data?.some((s) => s.status === "syncing" || s.repo_status === "indexing") ? 3000 : false) });
  const sync = useMutation({ mutationFn: (id: string) => api(`/wiki/sources/${id}/sync`, { method: "POST" }), onSuccess: refresh });
  const toast = useToast();
  const del = useMutation({ mutationFn: (id: string) => api(`/wiki/sources/${id}?delete_pages=true`, { method: "DELETE" }), onSuccess: () => { refresh(); toast("Source removed"); } });
  const schedule = useMutation({ mutationFn: (v: { id: string; hours: number }) => api(`/wiki/sources/${v.id}/schedule`, { method: "PUT", json: { sync_interval_hours: v.hours } }), onSuccess: refresh });
  const collections = (overview?.sources ?? []).filter((s) => s.kind === "collection");

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>Connected sources</CardTitle>
        <ErrorNote error={sync.error || del.error} />
        {sources.isLoading ? <Spinner /> : !sources.data?.length ? <Empty>Nothing connected yet. Connect Confluence, Jira or Slack below, or add documents and URLs.</Empty> : (
          <ul className="divide-y divide-line">{sources.data.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
              <div className="min-w-0 flex-1"><div className="font-medium">{s.name}</div>
                <div className="truncate text-xs text-muted"><Badge>{s.kind}</Badge> {s.label ?? s.url}{s.space_key ? ` · ${s.space_key}` : ""} · {s.pages} pages · {s.last_synced_at ? `synced ${timeAgo(s.last_synced_at)}` : "never synced"}</div>
                {s.error && <div role="alert" className="text-xs text-red-600">{s.error}</div>}</div>
              <Badge tone={statusTone(s.status)}>{s.status}</Badge><Badge tone={s.visibility === "restricted" ? "amber" : "gray"}>{s.visibility}</Badge>
              {admin && <select aria-label={`Sync schedule for ${s.name}`} className="h-8 rounded-md border border-line bg-white px-1.5 text-xs" value={s.sync_interval_hours ?? 0}
                onChange={(e) => schedule.mutate({ id: s.id, hours: +e.target.value })}>{SCHEDULES.map(([h, l]) => <option key={h} value={h}>{l}</option>)}</select>}
              {admin && <>
                <Button variant="outline" size="sm" aria-label={`Sync ${s.name}`} disabled={sync.isPending} onClick={() => sync.mutate(s.id)}><RefreshCw className="h-3.5 w-3.5" />Sync</Button>
                <Button variant="ghost" size="sm" aria-label={`Remove ${s.name}`} onClick={async () => { if (await ask({ title: `Remove "${s.name}"?`, message: "All pages imported from this source are deleted from the wiki. The original content is not touched.", confirmLabel: "Remove source", danger: true })) del.mutate(s.id); }}><Trash2 className="h-3.5 w-3.5" /></Button></>}
            </li>))}</ul>)}
      </Card>
      <Collections sources={overview?.sources ?? []} onChanged={refresh} />
      <div className="grid gap-4 lg:grid-cols-2">
        <AddDocs collections={collections.map((c) => ({ id: c.repo_id, name: c.name }))} onDone={refresh} />
        <ImportUrl collections={collections.map((c) => ({ id: c.repo_id, name: c.name }))} onDone={refresh} />
      </div>
      {admin && <ConnectorsCard onDone={refresh} />}
      {admin && <SlackExportCard onDone={refresh} />}
      {admin ? <ConfluenceForm onDone={refresh} /> : <Card><CardTitle>Confluence</CardTitle><p className="text-sm text-muted">Only admins can import from Confluence.</p></Card>}
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
  const conn = useQuery({ queryKey: ["confluence-conn"], queryFn: () => api<ConfluenceConn>("/wiki/confluence/connection") });
  const c = conn.data;
  return (
    <Card>
      <CardTitle right={c?.connected ? <Badge tone="green">connected</Badge> : <Badge>not connected</Badge>}>Confluence</CardTitle>
      {conn.isLoading ? <Spinner /> : c?.connected ? (<>
        <p className="text-sm text-muted">Connected to <span className="font-medium text-ink">{c.base_url}</span>. <Link to="/settings" className="text-brand hover:underline">Manage connection</Link></p>
        <ConfluenceImport onDone={onDone} />
        <ConfluenceBrowser onDone={onDone} />
      </>) : (
        <p className="text-sm text-muted">Connect Confluence in <Link to="/settings" className="text-brand hover:underline">Settings → Integrations</Link> with an API token, then import spaces and pages here.</p>)}
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

/** Jira, Slack and other connectors, rendered from the field list the server declares. Secrets are write-only. */
function ConnectorsCard({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [sp] = useSearchParams();
  const list = useQuery({ queryKey: ["wiki-connectors"], queryFn: () => api<ConnectorInfo[]>("/wiki/connectors") });
  const [kind, setKind] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const [hours, setHours] = useState(0);
  const [vis, setVis] = useState("restricted");
  const connectors = Array.isArray(list.data) ? list.data : [];
  const info = connectors.find((c) => c.kind === kind);
  useEffect(() => { // arriving from the Jira page with a query to import
    const k = sp.get("connector");
    if (k && connectors.some((c) => c.kind === k) && kind !== k) { setKind(k); setValues(sp.get("jql") ? { jql: sp.get("jql")! } : {}); }
  }, [connectors.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const split = () => {
    const config: Record<string, string> = {}; let secret = "";
    info?.fields.forEach((f) => { if (f.secret) secret = values[f.key] ?? ""; else if (values[f.key] !== undefined && values[f.key] !== "") config[f.key] = values[f.key]; });
    return { config, secret };
  };
  const check = useMutation({ mutationFn: () => api<{ ok: boolean; detail?: string; error?: string }>(`/wiki/connectors/${kind}/check`, { method: "POST", json: split() }) });
  const imp = useMutation({
    mutationFn: () => api(`/wiki/connectors/${kind}/import`, { method: "POST", json: { ...split(), name, visibility: vis, sync_interval_hours: hours } }),
    onSuccess: () => { toast("Import started. Pages appear as they are indexed."); setValues({}); setName(""); check.reset(); onDone(); },
  });
  const missing = info?.fields.some((f) => f.required && !(values[f.key] ?? "").trim());
  return (
    <Card>
      <CardTitle>Connect Jira, Slack and more</CardTitle>
      {list.isLoading ? <Spinner /> : (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{connectors.map((c) => (
          <button key={c.kind} type="button" aria-pressed={kind === c.kind} onClick={() => { setKind(c.kind); setValues({}); check.reset(); }}
            className={`rounded-lg border p-3 text-left hover:border-brand ${kind === c.kind ? "border-brand bg-indigo-50/50" : "border-line"}`}>
            <div className="text-sm font-medium">{c.label}</div><div className="mt-0.5 text-xs text-muted">{c.description}</div></button>))}</div>)}
      {info && (
        <form className="mt-4 grid gap-3 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); imp.mutate(); }}>
          {info.fields.map((f) => (
            <div key={f.key} className={f.type === "textarea" || f.key === "jql" ? "sm:col-span-2" : ""}>
              <Label>{f.label}{!f.required && " (optional)"}</Label>
              {f.type === "textarea"
                ? <Textarea rows={4} aria-label={f.label} value={values[f.key] ?? ""} placeholder={f.placeholder} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                : <Input aria-label={f.label} type={f.type === "password" ? "password" : f.type === "number" ? "number" : "text"} autoComplete="off" value={values[f.key] ?? ""} placeholder={f.placeholder} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />}
              {f.help && <p className="mt-1 text-xs text-muted">{f.help}</p>}
              {f.secret && <p className="mt-1 text-xs text-muted">Stored encrypted and never shown again.</p>}
            </div>))}
          <div><Label>Collection name (optional)</Label><Input aria-label="Collection name" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div><Label>Keep up to date</Label><select aria-label="Keep up to date" className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={hours} onChange={(e) => setHours(+e.target.value)}>{SCHEDULES.map(([h, l]) => <option key={h} value={h}>{l}</option>)}</select></div>
          <div><Label>Who can see it</Label><select aria-label="Visibility of imported content" className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={vis} onChange={(e) => setVis(e.target.value)}><option value="restricted">Restricted (grant access per source)</option><option value="org">Whole organization</option></select></div>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <Button type="button" variant="outline" disabled={missing || check.isPending} onClick={() => check.mutate()}>Test connection</Button>
            <Button disabled={missing || imp.isPending}>Import</Button>
            {check.data && <span role="status" className={`text-sm ${check.data.ok ? "text-emerald-700" : "text-red-600"}`}>{check.data.ok ? check.data.detail : check.data.error}</span>}
          </div>
          <div className="sm:col-span-2"><ErrorNote error={check.error || imp.error} /></div>
        </form>)}
    </Card>
  );
}

function SlackExportCard({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const imp = useMutation({
    mutationFn: async (files: FileList) => {
      const items = await Promise.all(Array.from(files).filter((f) => f.name.endsWith(".json") && f.size < 1_000_000).map(async (f) => ({ path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name, content: await f.text() })));
      return api<{ documents: number }>("/wiki/slack-export", { method: "POST", json: { name: "Slack export", files: items.slice(0, 400) } });
    },
    onSuccess: (r) => { toast(`Imported ${r.documents} conversation${r.documents === 1 ? "" : "s"}`); onDone(); },
  });
  return (
    <Card>
      <CardTitle>Import a Slack export</CardTitle>
      <p className="mb-2 text-xs text-muted">No bot token needed. Unzip a workspace export and choose its folder (or the JSON files). Threads and busy days become searchable notes. Not kept in sync.</p>
      <input type="file" multiple accept=".json" aria-label="Slack export files" className="text-sm"
        // @ts-expect-error webkitdirectory is a non-standard but widely supported attribute
        webkitdirectory="" onChange={(e) => e.target.files?.length && imp.mutate(e.target.files)} />
      <ErrorNote error={imp.error} />
    </Card>
  );
}

interface Space { key: string; name: string; type: string; import_url: string }
interface PageNode { id: string; title: string; parent_id: string; import_url: string }

/** Pick a space or page from what the connected account can see, instead of pasting a URL. */
function ConfluenceBrowser({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [space, setSpace] = useState<Space | null>(null);
  const [vis, setVis] = useState("restricted");
  const spaces = useQuery({ queryKey: ["confluence-spaces"], enabled: open, retry: false, queryFn: () => api<Space[]>("/wiki/confluence/spaces") });
  const tree = useQuery({ queryKey: ["confluence-pages", space?.key], enabled: !!space, retry: false, queryFn: () => api<{ truncated: boolean; pages: PageNode[] }>(`/wiki/confluence/spaces/${space!.key}/pages`) });
  const imp = useMutation({
    mutationFn: (v: { url: string; mode: string }) => api("/wiki/confluence/import", { method: "POST", json: { ...v, max_pages: 500, visibility: vis } }),
    onSuccess: () => { toast("Import started. Pages appear as they are indexed."); onDone(); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const kids = (id: string) => (tree.data?.pages ?? []).filter((p) => p.parent_id === id);
  const render = (p: PageNode, depth: number): React.ReactNode => (
    <li key={p.id}>
      <div className="flex items-center gap-2 py-0.5 text-sm" style={{ paddingLeft: depth * 14 }}><span className="min-w-0 flex-1 truncate">{p.title}</span>
        <button className="text-xs text-brand hover:underline" aria-label={`Import page ${p.title}`} disabled={imp.isPending} onClick={() => imp.mutate({ url: p.import_url, mode: "page" })}>page</button>
        {kids(p.id).length > 0 && <button className="text-xs text-brand hover:underline" aria-label={`Import ${p.title} and its children`} disabled={imp.isPending} onClick={() => imp.mutate({ url: p.import_url, mode: "tree" })}>+ children</button>}</div>
      {kids(p.id).length > 0 && <ul>{kids(p.id).map((c) => render(c, depth + 1))}</ul>}
    </li>
  );
  const ids = new Set((tree.data?.pages ?? []).map((p) => p.id));
  const roots = (tree.data?.pages ?? []).filter((p) => !p.parent_id || !ids.has(p.parent_id));
  return (
    <div className="mt-4 border-t border-line pt-4">
      <button className="text-sm font-semibold text-brand hover:underline" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{open ? "Hide space browser" : "Browse spaces instead of pasting a URL"}</button>
      {open && (
        <div className="mt-3 grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
          <div>{spaces.isLoading ? <Spinner /> : spaces.error ? <ErrorNote error={spaces.error} /> : (
            <ul className="max-h-72 space-y-0.5 overflow-auto" aria-label="Spaces">{spaces.data?.map((s) => (
              <li key={s.key}><button onClick={() => setSpace(s)} aria-current={space?.key === s.key} className={`w-full rounded px-2 py-1 text-left text-sm hover:bg-slate-50 ${space?.key === s.key ? "bg-indigo-50" : ""}`}>{s.name} <span className="text-xs text-muted">{s.key}</span></button></li>))}</ul>)}</div>
          <div className="min-w-0">
            {!space ? <p className="text-sm text-muted">Choose a space to see its pages.</p> : (<>
              <div className="mb-2 flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{space.name}</span>
                <Button size="sm" disabled={imp.isPending} onClick={() => imp.mutate({ url: space.import_url, mode: "space" })}>Import whole space</Button>
                <select aria-label="Visibility of browsed import" className="h-8 rounded-md border border-line bg-white px-2 text-xs" value={vis} onChange={(e) => setVis(e.target.value)}><option value="restricted">Restricted</option><option value="org">Whole organization</option></select></div>
              {tree.isLoading ? <Spinner /> : tree.error ? <ErrorNote error={tree.error} /> : (<>
                <ul className="max-h-72 overflow-auto rounded-lg border border-line p-2" aria-label="Pages">{roots.map((p) => render(p, 0))}</ul>
                {tree.data?.truncated && <p className="mt-1 text-xs text-amber-700">Showing the first 1,000 pages.</p>}</>)}
            </>)}
          </div>
        </div>)}
    </div>
  );
}
