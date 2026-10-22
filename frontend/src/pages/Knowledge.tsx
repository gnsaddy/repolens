import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Network, Search, Sparkles, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useConfirm, useToast } from "@/components/feedback";
import ForceGraph, { HEAT_GRADIENT, NODE_COLORS, REL_COLORS } from "@/components/ForceGraph";
import { Badge, Button, Card, CardTitle, CodeBlock, Empty, ErrorNote, Input, Label, PageHeader, Spinner, Stat, statusTone } from "@/components/ui";
import { api, type AskResult, type Connection, type DocRow, type GraphData, type GraphNode, type GraphStats, type HeatResult, type KnowledgeHit, type NodeDetail, type Repo } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const TABS = [["ask", "Ask & Search", Sparkles], ["graph", "Graph", Network], ["docs", "Documents", FileText]] as const;
type TabKey = (typeof TABS)[number][0];

export default function Knowledge() {
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as TabKey) || "ask";
  const go = (t: TabKey, extra: Record<string, string> = {}) => setSp({ tab: t, ...extra });
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories"),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "indexing") ? 2000 : false) });
  return (
    <div>
      <PageHeader title="Knowledge Base" subtitle="Documents, code and history connected in one graph — search it, ask it, explore it." />
      <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
        {TABS.map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => go(k)}
            className={cn("-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm", tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink")}>
            <Icon className="h-4 w-4" />{label}</button>))}
      </div>
      {tab === "ask" && <AskTab repos={repos.data ?? []} openNode={(id) => go("graph", { node: id })} />}
      {tab === "graph" && <GraphTab repos={repos.data ?? []} initialNode={sp.get("node")} />}
      {tab === "docs" && <DocsTab repos={repos.data ?? []} />}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Ask & Search
function ConnectionChips({ conns, onOpen }: { conns: Connection[]; onOpen: (id: string) => void }) {
  const shown = conns.filter((c) => c.rel !== "CONTAINS").slice(0, 5);
  if (!shown.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {shown.map((c, i) => (
        <button key={i} onClick={() => onOpen(c.node.id)} title={`${c.provenance}${c.evidence ? ` — ${c.evidence}` : ""}`}
          className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs hover:bg-slate-50">
          <span className="h-2 w-2 rounded-full" style={{ background: REL_COLORS[c.rel] }} />
          <span className="text-muted">{c.label}</span><span className="max-w-[16rem] truncate font-medium">{c.node.qualname || c.node.name}</span>
        </button>))}
    </div>
  );
}

function AskTab({ repos, openNode }: { repos: Repo[]; openNode: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [repoId, setRepoId] = useState("");
  const [kind, setKind] = useState<"" | "doc" | "code" | "pr">("");
  const ask = useMutation({ mutationFn: (fresh: boolean = false) => api<AskResult>("/knowledge/ask", { method: "POST", json: { query: q, repo_ids: repoId ? [repoId] : [], fresh } }) });
  const search = useMutation({ mutationFn: () => api<KnowledgeHit[]>("/knowledge/search", { method: "POST", json: { query: q, repo_ids: repoId ? [repoId] : [], kinds: kind ? [kind] : [], k: 10 } }) });
  const submit = (e: FormEvent) => { e.preventDefault(); if (q.trim().length > 1) { ask.mutate(false); search.mutate(); } };
  const a = ask.data;
  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="flex flex-wrap gap-2">
        <Input className="min-w-64 flex-1" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. How are deleted users handled? Where is get_profile documented?" aria-label="Question" />
        <select aria-label="Scope" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
          <option value="">All sources</option>{repos.filter((r) => r.status === "ready").map((r) => <option key={r.id} value={r.id}>{r.name}{r.source === "docs" ? " (docs)" : ""}</option>)}</select>
        <select aria-label="Kind" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={kind} onChange={(e) => setKind(e.target.value as "" | "doc" | "code" | "pr")}>
          <option value="">Docs + code + PRs</option><option value="doc">Docs only</option><option value="code">Code only</option><option value="pr">Pull requests only</option></select>
        <Button disabled={ask.isPending || search.isPending}><Search className="h-4 w-4" />Ask</Button>
      </form>
      <ErrorNote error={ask.error ?? search.error} />
      {a && (
        <Card>
          <CardTitle right={<div className="flex gap-1">
            <Badge tone={a.mode === "llm" ? "brand" : "gray"}>{a.mode === "llm" ? "AI answer" : a.mode === "extractive" ? "extracted passages" : "no results"}</Badge>
            {a.memory ? <Badge tone="green" title={`Matched an earlier question: "${a.memory.asked}"`}>From memory · saved ~{a.memory.tokens_saved.toLocaleString()} tokens</Badge> : a.cached && <Badge tone="green">cached</Badge>}
            {a.route && <Badge tone="gray" title={a.route.why}>{a.route.model}</Badge>}</div>}>Answer</CardTitle>
          <p className="whitespace-pre-wrap text-sm leading-6">{a.answer}</p>
          {(a.memory || a.cached) && <button type="button" className="mt-2 text-xs text-brand hover:underline" disabled={ask.isPending} onClick={() => ask.mutate(true)}>Answer again with the model</button>}
          {!!a.unknown_citations?.length && <p className="mt-2 text-xs text-amber-700">The model cited unknown sources ({a.unknown_citations.join(", ")}) — treat those claims as unverified.</p>}
          {!!a.connections.length && (<div className="mt-4 border-t border-line pt-3">
            <div className="mb-1 text-xs font-medium text-muted">Connections found in the graph</div>
            <ul className="space-y-0.5 text-xs">{a.connections.slice(0, 6).map((c, i) => (
              <li key={i}><span className="font-medium">{c.from}</span> <span className="text-muted">{c.label}</span> <code>{c.to}</code> <span className="text-muted">({c.provenance.replace("_", " ")})</span></li>))}</ul></div>)}
        </Card>)}
      {search.data && (
        <div className="space-y-3">
          <div className="text-xs font-medium text-muted">Sources ({search.data.length})</div>
          {!search.data.length && <Empty>No matching passages.</Empty>}
          {search.data.map((h, i) => (
            <Card key={h.chunk_id} className="p-4">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted">S{i + 1}</span>
                <Badge tone={h.kind === "doc" ? "blue" : h.kind === "pr" ? "green" : "brand"}>{h.kind === "pr" ? "pull request" : h.kind}</Badge>
                {h.via_graph && <Badge tone="amber" title={`${h.via_graph.relation} ${h.via_graph.from}`}>via graph · {h.via_graph.relation} “{h.via_graph.from.slice(0, 30)}”</Badge>}
                <span className="text-sm font-medium">{h.title}</span>
                {h.url && /^https?:\/\//.test(h.url) ? <a href={h.url} target="_blank" rel="noopener noreferrer" className="text-xs text-brand hover:underline">{h.repo}:{h.file}:{h.start_line}–{h.end_line}</a> : <span className="text-xs text-muted">{h.repo}:{h.file}:{h.start_line}–{h.end_line}</span>}
                {h.node_id && <button className="ml-auto text-xs text-brand hover:underline" onClick={() => openNode(h.node_id!)}>Open in graph</button>}
              </div>
              <pre className="max-h-32 overflow-hidden whitespace-pre-wrap rounded bg-slate-50 p-2 font-mono text-xs">{h.content.slice(0, 500)}</pre>
              <ConnectionChips conns={h.connections} onOpen={openNode} />
            </Card>))}
        </div>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Graph
const ALL_TYPES = ["file", "doc", "section", "function", "class", "commit", "pr"];

function GraphTab({ repos, initialNode }: { repos: Repo[]; initialNode: string | null }) {
  const [repoId, setRepoId] = useState("");
  const [types, setTypes] = useState<string[]>(["file", "doc", "section", "function", "class"]);
  const [selected, setSelected] = useState<string | null>(initialNode);
  const [extra, setExtra] = useState<GraphData>({ nodes: [], edges: [] });
  const [query, setQuery] = useState("");
  const [pathTarget, setPathTarget] = useState<GraphNode | null>(null);
  const [colorBy, setColorBy] = useState("type");
  const [hiddenRels, setHiddenRels] = useState<string[]>([]);
  const [pathSet, setPathSet] = useState<{ nodes: Set<string>; edges: Set<string> } | undefined>();

  const typesKey = types.join();
  const stats = useQuery({ queryKey: ["graph-stats", repoId], queryFn: () => api<GraphStats>(`/graph/stats${repoId ? `?repo_id=${repoId}` : ""}`) });
  const overview = useQuery({ queryKey: ["graph-overview", repoId, typesKey], queryFn: () => api<GraphData>(`/graph/overview?types=${types.join(",")}&limit=120${repoId ? `&repo_id=${repoId}` : ""}`) });
  const detail = useQuery({ queryKey: ["graph-node", selected], enabled: !!selected, queryFn: () => api<NodeDetail>(`/graph/nodes/${selected}?depth=1`) });
  const found = useQuery({ queryKey: ["graph-find", query], enabled: query.length > 1, queryFn: () => api<GraphNode[]>(`/graph/nodes?q=${encodeURIComponent(query)}&limit=8`) });

  useEffect(() => { setExtra({ nodes: [], edges: [] }); setPathSet(undefined); }, [repoId, typesKey]);
  const merged = useMemo<GraphData>(() => {
    const nodes = new Map<string, GraphNode>(); const edges = new Map<string, GraphData["edges"][number]>();
    [overview.data, extra].forEach((g) => { g?.nodes.forEach((n) => nodes.set(n.id, n)); g?.edges.forEach((e) => edges.set(e.id, e)); });
    return { nodes: [...nodes.values()], edges: [...edges.values()] };
  }, [overview.data, extra]);

  const shownEdges = useMemo(() => merged.edges.filter((e) => !hiddenRels.includes(e.rel)), [merged.edges, hiddenRels]);
  const nodeIds = useMemo(() => merged.nodes.map((n) => n.id).slice(0, 500), [merged.nodes]);
  const heat = useQuery({ queryKey: ["graph-heat", colorBy, nodeIds.join()], enabled: colorBy !== "type" && nodeIds.length > 0,
    queryFn: () => api<HeatResult>("/graph/heat", { method: "POST", json: { metric: colorBy, node_ids: nodeIds, days: 90 } }) });
  const toggleRel = (r: string) => setHiddenRels((h) => (h.includes(r) ? h.filter((x) => x !== r) : [...h, r]));

  const expand = useCallback(() => { if (detail.data) setExtra((x) => ({ nodes: [...x.nodes, ...detail.data.nodes], edges: [...x.edges, ...detail.data.edges] })); }, [detail.data]);
  useEffect(() => { if (initialNode && detail.data && detail.data.node.id === initialNode) expand(); }, [initialNode, detail.data, expand]);

  const tracePath = async (target: GraphNode) => {
    setPathTarget(target);
    if (!selected) return;
    const r = await api<{ found: boolean; steps: { node: GraphNode; edge: { id: string } | null }[] }>(`/graph/path?src=${selected}&dst=${target.id}`);
    if (!r.found) { setPathSet({ nodes: new Set(), edges: new Set() }); return; }
    setExtra((x) => ({ nodes: [...x.nodes, ...r.steps.map((s) => s.node)], edges: x.edges }));
    setPathSet({ nodes: new Set(r.steps.map((s) => s.node.id)), edges: new Set(r.steps.flatMap((s) => (s.edge ? [s.edge.id] : []))) });
    const ids = r.steps.map((s) => s.node.id);
    const sub = await Promise.all(ids.map((id) => api<NodeDetail>(`/graph/nodes/${id}?depth=1`)));
    setExtra((x) => ({ nodes: [...x.nodes, ...sub.flatMap((d) => d.nodes.filter((n) => ids.includes(n.id)))], edges: [...x.edges, ...sub.flatMap((d) => d.edges.filter((e) => ids.includes(e.source) && ids.includes(e.target)))] }));
  };

  const toggle = (t: string) => setTypes((s) => (s.includes(t) ? s.filter((x) => x !== t) : [...s, t]));
  const s = stats.data;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Nodes" value={s?.total_nodes ?? "—"} hint={s ? Object.entries(s.nodes).map(([k, v]) => `${v} ${k}`).join(" · ") : undefined} />
        <Stat label="Connections" value={s?.total_edges ?? "—"} />
        <Stat label="From static analysis" value={s?.edges.filter((e) => e.provenance === "static_analysis").reduce((a, e) => a + e.count, 0) ?? "—"} hint="calls, imports, tests" />
        <Stat label="From docs & semantics" value={s?.edges.filter((e) => ["doc_mention", "doc_link", "semantic"].includes(e.provenance)).reduce((a, e) => a + e.count, 0) ?? "—"} hint="mentions, links, similarity" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="Repository" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
          <option value="">All sources</option>{repos.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
        {ALL_TYPES.map((t) => (
          <label key={t} className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={types.includes(t)} onChange={() => toggle(t)} />
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: NODE_COLORS[t] }} />{t}</label>))}
        <div className="relative ml-auto w-64">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a symbol, file or section…" aria-label="Find node" />
          {query.length > 1 && found.data && (
            <ul className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-md border border-line bg-white shadow-lg">
              {found.data.map((n) => (<li key={n.id}><button className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-50"
                onClick={() => { setSelected(n.id); setExtra((x) => ({ nodes: [...x.nodes, n], edges: x.edges })); setQuery(""); }}>
                <span className="h-2 w-2 rounded-full" style={{ background: NODE_COLORS[n.type] }} /><span className="truncate">{n.qualname || n.name}</span><span className="ml-auto text-muted">{n.type}</span></button></li>))}
              {!found.data.length && <li className="px-3 py-2 text-xs text-muted">No matches</li>}</ul>)}
        </div>
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <div>
          {overview.isLoading ? <Spinner /> : !merged.nodes.length ? <Empty>The graph is empty. Index a repository or upload documents first.</Empty> : (
            <ForceGraph heat={colorBy !== "type" ? (heat.data?.values ?? {}) : undefined} nodes={merged.nodes} edges={shownEdges} selectedId={selected} highlight={pathSet ? new Set([...pathSet.nodes, ...pathSet.edges]) : undefined} onSelect={setSelected} />)}
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
            <label className="flex items-center gap-1.5">Colour by
              <select aria-label="Colour by" className="h-8 rounded-md border border-line bg-white px-2" value={colorBy} onChange={(e) => setColorBy(e.target.value)}>
                <option value="type">Node type</option><option value="churn">Heat: recent change (90 days)</option><option value="bugs">Heat: bug-fix activity</option>
                <option value="connections">Heat: most connected</option><option value="undocumented">Heat: undocumented code</option></select></label>
            {colorBy !== "type" && (<span className="flex items-center gap-2 text-muted" aria-label="Heat legend">
              {heat.isLoading ? "Loading…" : (<><span>cold</span><span className="h-2.5 w-28 rounded" style={{ background: HEAT_GRADIENT }} /><span>hot</span><span>· {heat.data?.label}</span>{heat.data && !Object.keys(heat.data.values).length && <span className="text-amber-700">· no data for these nodes</span>}</>)}</span>)}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
            {Object.entries(REL_COLORS).filter(([r]) => r !== "INCLUDES").map(([r, c]) => (
              <label key={r} className="flex items-center gap-1"><input type="checkbox" aria-label={`Show ${r.toLowerCase().replace("_", " ")}`} checked={!hiddenRels.includes(r)} onChange={() => toggleRel(r)} />
                <span className="h-0.5 w-4" style={{ background: c }} />{r.toLowerCase().replace("_", " ")}</label>))}
            <span>· arrows point from caller/source to target · dashed = semantic guess · hover a node to label its links · scroll to zoom, drag to pan</span></div>
        </div>
        <Card className="max-h-[36rem] overflow-auto">
          {!selected ? <Empty>Select a node to see what it connects to.</Empty> : !detail.data ? <Spinner /> : (<>
            <div className="mb-1 flex items-center gap-2"><Badge tone="gray">{detail.data.node.type}</Badge><span className="text-xs text-muted">{detail.data.node.repo}</span></div>
            <h3 className="break-words text-sm font-semibold">{detail.data.node.qualname || detail.data.node.name}</h3>
            {detail.data.node.path && <div className="mb-2 text-xs text-muted">{detail.data.node.path}{detail.data.node.start_line ? `:${detail.data.node.start_line}–${detail.data.node.end_line}` : ""}</div>}
            <div className="mb-3 flex gap-2"><Button size="sm" variant="outline" onClick={expand}>Expand neighbours</Button>
              {pathTarget && <Badge tone="amber">path → {pathTarget.name}</Badge>}</div>
            {detail.data.content && <div className="mb-3 max-h-40 overflow-auto"><CodeBlock code={detail.data.content.split("\n").slice(0, 25).join("\n")} startLine={detail.data.node.start_line || 1} /></div>}
            <div className="mb-1 text-xs font-medium text-muted">Connections ({detail.data.connections.length})</div>
            <div className="mb-2 flex flex-wrap gap-1">{Object.entries(detail.data.connections.reduce<Record<string, number>>((m, c) => ({ ...m, [c.label]: (m[c.label] ?? 0) + 1 }), {})).map(([l, n]) => <Badge key={l}>{n} {l}</Badge>)}</div>
            <ul className="space-y-1">{[...detail.data.connections].sort((a, b) => a.label.localeCompare(b.label)).map((c, i) => (
              <li key={i} className="flex items-center gap-1 text-xs">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: REL_COLORS[c.rel] }} />
                <span className="w-20 shrink-0 text-muted">{c.label}</span>
                <button className="truncate text-left font-medium text-brand hover:underline" onClick={() => { setSelected(c.node.id); setExtra((x) => ({ nodes: [...x.nodes, c.node], edges: x.edges })); }}>{c.node.qualname || c.node.name}</button>
                <button className="ml-auto shrink-0 text-muted hover:text-ink" title="Trace the shortest path from the selected node to this one" onClick={() => tracePath(c.node)}>⇢</button>
              </li>))}</ul>
          </>)}
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Documents
const ACCEPT = ".md,.mdx,.rst,.txt,.adoc";

function DocsTab({ repos }: { repos: Repo[] }) {
  const ask = useConfirm();
  const toast = useToast();
  const { user } = useAuth();
  const qc = useQueryClient();
  const collections = repos.filter((r) => r.source === "docs");
  const [sel, setSel] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const active = collections.find((c) => c.id === sel) ?? collections[0];
  const docs = useQuery({ queryKey: ["docs", active?.id, active?.status, active?.chunk_count], enabled: !!active, queryFn: () => api<DocRow[]>(`/knowledge/collections/${active!.id}/documents`) });
  const create = useMutation({ mutationFn: () => api<Repo>("/knowledge/collections", { method: "POST", json: { name } }),
    onSuccess: (r) => { setName(""); setSel(r.id); qc.invalidateQueries({ queryKey: ["repos"] }); } });
  const upload = useMutation({ mutationFn: (documents: { path: string; content: string }[]) => api(`/knowledge/collections/${active!.id}/documents`, { method: "POST", json: { documents } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["repos"] }); qc.invalidateQueries({ queryKey: ["docs"] }); } });
  const remove = useMutation({ mutationFn: (id: string) => api(`/knowledge/collections/${active!.id}/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["repos"] }); qc.invalidateQueries({ queryKey: ["docs"] }); } });

  async function onFiles(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length || !active) return;
    const big = files.filter((f) => f.size > 1_000_000);
    const ok = files.filter((f) => f.size <= 1_000_000);
    setNote(big.length ? `Skipped ${big.length} file(s) over 1 MB.` : "");
    const documents = await Promise.all(ok.map(async (f) => ({ path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name, content: await f.text() })));
    if (documents.length) upload.mutate(documents);
  }
  const dropCollection = useMutation({ mutationFn: (id: string) => api(`/knowledge/collections/${id}`, { method: "DELETE" }),
    onSuccess: () => { setSel(null); qc.invalidateQueries({ queryKey: ["repos"] }); toast("Collection deleted"); } });
  const writable = canWrite(user);
  return (
    <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
      <div className="space-y-4">
        <Card><CardTitle>Collections</CardTitle>
          {!collections.length ? <Empty>No document collections yet.</Empty> : (
            <ul className="space-y-1">{collections.map((c) => (
              <li key={c.id}><button onClick={() => setSel(c.id)} className={cn("flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm hover:bg-slate-50", active?.id === c.id && "bg-indigo-50")}>
                <span className="truncate">{c.name}</span><Badge tone={statusTone(c.status)}>{c.status}</Badge></button></li>))}</ul>)}
        </Card>
        {writable && (<Card><CardTitle>New collection</CardTitle>
          <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="space-y-3">
            <label className="block"><Label>Name</Label><Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Engineering handbook" /></label>
            <ErrorNote error={create.error} /><Button className="w-full" disabled={create.isPending}>Create</Button></form></Card>)}
      </div>
      <Card>
        {!active ? <Empty>Create a collection, then upload Markdown, reStructuredText or text files. They are split by heading, linked to your code and added to the graph.</Empty> : (<>
          <CardTitle right={writable && (<label className="inline-flex cursor-pointer items-center gap-2 rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-600">
            <Upload className="h-4 w-4" />Upload files<input type="file" multiple accept={ACCEPT} className="sr-only" onChange={onFiles} aria-label="Upload documents" /></label>)}>{active.name}
            {user?.role === "admin" && <button aria-label="Delete collection" title="Delete this collection and all its documents" className="ml-2 align-middle text-muted hover:text-red-600"
              onClick={async () => { if (await ask({ title: `Delete the collection "${active.name}"?`, message: `All ${active.file_count} documents, their search entries and graph nodes are removed.`, confirmLabel: "Delete collection", danger: true })) dropCollection.mutate(active.id); }}><Trash2 className="inline h-4 w-4" /></button>}</CardTitle>
          <div className="mb-3 text-xs text-muted">{active.file_count} documents · {active.chunk_count} sections · accepts {ACCEPT.replaceAll(",", " ")} (max 1 MB each)</div>
          <ErrorNote error={upload.error ?? remove.error} />
          {note && <p className="mb-2 text-xs text-amber-700">{note}</p>}
          {(upload.isPending || active.status === "indexing") && <div className="mb-2 flex items-center gap-2 text-xs text-muted"><Spinner />Indexing and linking…</div>}
          {!docs.data?.length ? <Empty>No documents yet.</Empty> : (
            <table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-medium">Path</th><th className="font-medium">Size</th><th /></tr></thead>
              <tbody>{docs.data.map((d) => (<tr key={d.id} className="border-t border-line"><td className="py-2">{d.path}</td><td className="text-muted">{(d.size / 1024).toFixed(1)} KB</td>
                <td className="text-right">{writable && d.deletable !== false && <button aria-label={`Delete ${d.path}`} className="text-muted hover:text-red-600" onClick={async () => { if (await ask({ title: `Delete ${d.path}?`, message: "It is removed from search, the graph and the wiki.", confirmLabel: "Delete document", danger: true })) remove.mutate(d.id); }}><Trash2 className="h-4 w-4" /></button>}{writable && d.deletable === false && <span className="text-xs text-muted" title="Synced from a connected source; remove the source to delete it">synced</span>}</td></tr>))}</tbody></table>)}
        </>)}
      </Card>
    </div>
  );
}
