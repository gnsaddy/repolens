import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, Map as MapIcon, Pencil, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useToast } from "@/components/feedback";
import StartHere from "@/components/StartHere";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Spinner, Textarea } from "@/components/ui";
import { api, type Atlas, type PageHit, type PageSource, type WikiSource } from "@/lib/api";
import { cn } from "@/lib/utils";

type Open = (repoId: string, path: string, anchor?: string) => void;

/** Find a page by meaning from anywhere in the wiki. */
export function PageSearch({ open }: { open: Open }) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const hits = useQuery({ queryKey: ["wiki-search", q], enabled: q.length > 1, queryFn: () => api<PageHit[]>(`/wiki/search?q=${encodeURIComponent(q)}`) });
  const submit = (e: FormEvent) => { e.preventDefault(); setQ(text.trim()); };
  return (
    <div className="mb-4">
      <form onSubmit={submit} role="search" className="flex gap-2">
        <div className="relative flex-1"><Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" />
          <Input aria-label="Search pages" className="pl-9" placeholder="Search every page by meaning — e.g. how do we roll back a deploy?" value={text} onChange={(e) => { setText(e.target.value); if (!e.target.value) setQ(""); }} /></div>
        <Button variant="outline" disabled={text.trim().length < 2}>Search</Button>
      </form>
      {q && (
        <Card className="mt-2 p-2">
          {hits.isLoading ? <Spinner /> : hits.error ? <ErrorNote error={hits.error} /> : !hits.data?.length ? <p className="p-2 text-sm text-muted">No page matches “{q}”.</p> : (
            <ul aria-label="Search results" className="divide-y divide-line">{hits.data.map((h) => (
              <li key={h.repo_id + h.path}><button className="block w-full rounded px-2 py-2 text-left hover:bg-slate-50" onClick={() => { open(h.repo_id, h.path); setQ(""); setText(""); }}>
                <span className="text-sm font-medium text-brand">{h.title}</span> <span className="text-xs text-muted">{h.repo} · {h.path}</span>
                <span className="mt-0.5 block text-xs text-muted">{h.snippet}</span></button></li>))}</ul>)}
        </Card>)}
    </div>
  );
}

/** Edit an existing page or write a new one, inside a documentation collection. */
export function PageEditor({ repoId, path, onDone }: { repoId: string; path: string | null; onDone: (path: string) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [newPath, setNewPath] = useState("");
  const [text, setText] = useState<string | null>(path ? null : "# New page\n\n");
  const src = useQuery({ queryKey: ["wiki-source", repoId, path], enabled: !!path, queryFn: () => api<PageSource>(`/wiki/page/source?repo_id=${repoId}&path=${encodeURIComponent(path!)}`) });
  const target = path ?? (newPath.trim().match(/\.(md|mdx|rst|txt|adoc)$/i) ? newPath.trim() : `${newPath.trim()}.md`);
  const value = text ?? src.data?.content ?? "";
  const save = useMutation({
    mutationFn: () => api<{ path: string; created: boolean }>("/wiki/page", { method: "PUT", json: { repo_id: repoId, path: target, content: value } }),
    onSuccess: (r) => {
      for (const k of ["wiki-overview", "wiki-tree", "wiki-page", "wiki-source", "wiki-search"]) qc.invalidateQueries({ queryKey: [k] });
      toast(r.created ? "Page created. Indexing…" : "Saved. Re-indexing…");
      onDone(r.path);
    },
  });
  if (path && src.isLoading) return <Spinner />;
  if (path && src.data && !src.data.editable) return <p role="alert" className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">{src.data.reason}</p>;
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      {!path && <label className="block text-sm font-medium">Page path<Input aria-label="Page path" className="mt-1" value={newPath} onChange={(e) => setNewPath(e.target.value)} placeholder="runbooks/restart-api.md" /></label>}
      <Textarea aria-label="Page content" className="h-96 font-mono text-sm" value={value} onChange={(e) => setText(e.target.value)} />
      <p className="text-xs text-muted">Markdown. Link pages with [[Page title]]. Secrets are removed automatically. The page is re-indexed and linked to code when you save.</p>
      <ErrorNote error={save.error} />
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => onDone(path ?? "")}>Cancel</Button><Button disabled={save.isPending || (!path && !newPath.trim()) || !value.trim()}>{save.isPending ? "Saving…" : "Save page"}</Button></div>
    </form>
  );
}

export function NewPageButton({ source, onClick }: { source?: WikiSource; onClick: () => void }) {
  if (!source || source.kind !== "collection" || source.origin === "confluence") return null;
  return <Button size="sm" variant="outline" onClick={onClick}><FilePlus2 className="h-3.5 w-3.5" />New page</Button>;
}

export function EditButton({ onClick }: { onClick: () => void }) {
  return <button className="inline-flex items-center gap-1 text-brand hover:underline" onClick={onClick}><Pencil className="h-3.5 w-3.5" />Edit</button>;
}

/** A map of a code repository: modules, their key symbols, and how much of it the wiki explains. */
export function AtlasTab({ sources }: { sources: WikiSource[] }) {
  const repos = sources.filter((s) => s.kind === "repo");
  const [sel, setSel] = useState<string>("");
  const repoId = sel || repos[0]?.repo_id || "";
  const atlas = useQuery({ queryKey: ["wiki-atlas", repoId], enabled: !!repoId, queryFn: () => api<Atlas>(`/wiki/atlas?repo_id=${repoId}`) });
  if (!repos.length) return <Empty>Add a code repository to see its atlas: modules, key symbols and documentation coverage.</Empty>;
  const a = atlas.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="Repository" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={repoId} onChange={(e) => setSel(e.target.value)}>{repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name}</option>)}</select>
        {a && <p className="text-sm text-muted"><MapIcon className="mr-1 inline h-4 w-4" />{a.summary.modules} modules · {a.summary.files} files · {a.summary.public_symbols} public symbols · <b className="text-ink">{a.summary.coverage != null ? `${Math.round(a.summary.coverage * 100)}%` : "—"}</b> documented</p>}
      </div>
      {repoId && <StartHere repoId={repoId} />}
      {atlas.isLoading ? <Spinner /> : atlas.error ? <ErrorNote error={atlas.error} /> : !a?.modules.length ? <Empty>Nothing indexed yet.</Empty> : (
        <div className="grid gap-3 md:grid-cols-2">
          {a.modules.map((m) => (
            <Card key={m.module} className="p-4">
              <CardTitle right={m.coverage != null ? <Badge tone={m.coverage >= 0.7 ? "green" : m.coverage >= 0.4 ? "amber" : "red"}>{Math.round(m.coverage * 100)}% documented</Badge> : undefined}>{m.module}</CardTitle>
              <p className="mb-2 text-xs text-muted">{m.files} files · {m.classes} classes · {m.functions} functions{m.languages.length ? ` · ${m.languages.join(", ")}` : ""}</p>
              {m.coverage != null && <div className="mb-3 h-1.5 overflow-hidden rounded bg-slate-100"><div className={cn("h-full", m.coverage >= 0.7 ? "bg-emerald-500" : m.coverage >= 0.4 ? "bg-amber-500" : "bg-red-500")} style={{ width: `${Math.round(m.coverage * 100)}%` }} /></div>}
              <ul className="space-y-1 text-sm">{m.top_symbols.map((s) => (
                <li key={s.node_id} className="flex items-center gap-2"><Link className="truncate text-brand hover:underline" to={`/knowledge?tab=graph&node=${s.node_id}`}><code className="text-xs">{s.name}</code></Link>
                  <span className="text-xs text-muted">{s.connections} links</span>{!s.documented && <Badge tone="amber">undocumented</Badge>}</li>))}</ul>
            </Card>))}
        </div>)}
    </div>
  );
}
