import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Check, FileEdit, Map as MapIcon, ChevronDown, Trash2, ChevronRight, Database, ExternalLink, HeartPulse, Lightbulb, Sparkles } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, PageHeader, Spinner, Stat, Textarea } from "@/components/ui";
import SimilarAnswers from "@/components/SimilarAnswers";
import { api, type SimilarAnswer, type AskResult, type ConceptDetail, type ConceptRow, type DriftAlertRow, type ProposalDetail, type ProposalRow, type PageInsights, type PageRef, type WikiHealth, type WikiOverview, type WikiPage, type WikiSource, type WikiTreeNode } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";
import { useConfirm, useToast } from "@/components/feedback";
import Markdown, { slug } from "./wiki/Markdown";
import Sources from "./wiki/Sources";
import { AtlasTab, EditButton, NewPageButton, PageEditor, PageSearch } from "./wiki/WikiCore";

const TABS = [["browse", "Browse", BookOpen], ["ask", "Ask", Sparkles], ["atlas", "Code atlas", MapIcon], ["concepts", "Concepts", Lightbulb], ["health", "Health", HeartPulse], ["drift", "Drift", AlertTriangle], ["proposals", "Proposals", FileEdit], ["sources", "Sources", Database]] as const;
type Tab = (typeof TABS)[number][0];
type Open = (repoId: string, path: string, anchor?: string) => void;

const ORIGIN_TONE: Record<string, "brand" | "blue" | "green" | "gray"> = { confluence: "brand", repo: "gray", collection: "blue", url: "green", upload: "blue" };

export default function Wiki() {
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "browse";
  const overview = useQuery({ queryKey: ["wiki-overview"], queryFn: () => api<WikiOverview>("/wiki/overview"), refetchInterval: (q) => (q.state.data?.sources.some((s) => s.status === "indexing") ? 3000 : false) });
  const drift = useQuery({ queryKey: ["drift-count"], queryFn: () => api<{ open: number }>("/wiki/drift/count"), refetchInterval: 60_000 });
  const go = (t: Tab, extra: Record<string, string> = {}) => setSp({ tab: t, ...extra });
  const open: Open = (repo, path, anchor) => setSp({ tab: "browse", repo, path, ...(anchor ? { a: anchor } : {}) });
  const o = overview.data;
  const issues = o ? o.health.orphans + o.health.broken_links + o.health.stale + o.health.duplicates + o.health.outdated_references : 0;

  return (
    <div>
      <PageHeader title="DeepMind Wiki" subtitle="One connected knowledge base over READMEs, uploaded docs, web pages and Confluence — linked to your code." />
      <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Sources" value={o?.totals.sources ?? "—"} />
        <Stat label="Pages" value={o?.totals.pages ?? "—"} hint={o ? `${o.totals.sections} sections` : undefined} />
        <Stat label="Concepts" value={o?.concepts ?? "—"} hint="terms that recur across docs" />
        <Stat label="Code documented" value={o?.health.documented_code_ratio != null ? `${Math.round(o.health.documented_code_ratio * 100)}%` : "—"} hint="public symbols named in docs" />
        <Stat label="Open issues" value={o ? issues : "—"} hint="orphans, broken links, stale…" />
      </div>
      <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
        {TABS.map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => go(k)} className={cn("-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm", tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink")}>
            <Icon className="h-4 w-4" />{label}{k === "drift" && !!drift.data?.open && <span className="rounded-full bg-amber-100 px-1.5 text-xs text-amber-800">{drift.data.open}</span>}</button>))}
      </div>
      {tab === "browse" && <Browse sources={o?.sources ?? []} repo={sp.get("repo")} path={sp.get("path")} anchor={sp.get("a")} open={open} openConcept={(t) => go("concepts", { term: t })} onAdd={() => go("sources")} />}
      {tab === "ask" && <AskWiki open={open} />}
      {tab === "atlas" && <AtlasTab sources={o?.sources ?? []} />}
      {tab === "concepts" && <Concepts initial={sp.get("term")} open={open} />}
      {tab === "health" && <Health open={open} />}
      {tab === "drift" && <Drift open={open} review={(id) => go("proposals", { id })} />}
      {tab === "proposals" && <Proposals selectedId={sp.get("id")} open={open} />}
      {tab === "sources" && <Sources overview={o} />}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ browse
function TreeNode({ node, depth, selected, onPick }: { node: WikiTreeNode; depth: number; selected: string | null; onPick: (path: string) => void }) {
  const [openState, setOpen] = useState(depth < 1);
  const has = node.children.length > 0;
  return (
    <li>
      <div className={cn("flex items-center gap-1 rounded py-0.5 pr-1 text-sm hover:bg-slate-50", node.path && node.path === selected && "bg-indigo-50 font-medium")} style={{ paddingLeft: depth * 12 }}>
        {has ? <button aria-label={openState ? "Collapse" : "Expand"} onClick={() => setOpen(!openState)} className="text-muted">{openState ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}</button> : <span className="w-3.5" />}
        {node.path ? <button className="truncate text-left" onClick={() => onPick(node.path!)} title={node.path}>{node.title}</button> : <span className="truncate text-muted">{node.title}</span>}
      </div>
      {has && openState && <ul>{node.children.map((c) => <TreeNode key={c.key} node={c} depth={depth + 1} selected={selected} onPick={onPick} />)}</ul>}
    </li>
  );
}

function AtAGlance({ i }: { i: PageInsights }) {
  const any = i.purpose || i.commands.length || i.endpoints.length || i.settings.length || i.owners.length || i.warnings.length;
  if (!any) return null;
  return (
    <Card className="p-4" aria-label="At a glance"><CardTitle>At a glance</CardTitle>
      <div className="space-y-3 text-sm">
        {i.purpose && <p className="leading-5">{i.purpose}</p>}
        {!!i.warnings.length && <ul className="space-y-1">{i.warnings.map((w, k) => <li key={k} className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900"><b className="capitalize">{w.level}:</b> {w.text}</li>)}</ul>}
        {!!i.owners.length && <div className="text-xs"><span className="text-muted">Who to ask: </span>{i.owners.map((o) => `${o.who} (${o.role})`).join(", ")}</div>}
        {!!i.commands.length && <div><div className="mb-1 text-xs text-muted">Commands{i.steps ? ` · ${i.steps} steps` : ""}</div><pre className="max-h-32 overflow-auto rounded bg-slate-900 p-2 font-mono text-xs text-slate-100">{i.commands.join("\n")}</pre></div>}
        {!!i.endpoints.length && <div className="text-xs"><span className="text-muted">Endpoints: </span>{i.endpoints.map((e) => <code key={e} className="mr-1.5">{e}</code>)}</div>}
        {!!i.settings.length && <div className="text-xs"><span className="text-muted">Settings: </span>{i.settings.map((e) => <code key={e} className="mr-1.5">{e}</code>)}</div>}
        {i.last_dated && <div className="text-xs text-muted">Last dated {i.last_dated}</div>}
      </div>
    </Card>
  );
}

function RefList({ title, items, open, empty }: { title: string; items: PageRef[]; open: Open; empty?: string }) {
  if (!items.length && !empty) return null;
  return (
    <Card className="p-4"><CardTitle>{title}</CardTitle>
      {!items.length ? <p className="text-xs text-muted">{empty}</p> : <ul className="space-y-1.5 text-sm">{items.map((r) => (
        <li key={r.repo_id + r.path}><button className="text-left text-brand hover:underline" onClick={() => open(r.repo_id, r.path)}>{r.title}</button>
          <div className="text-xs text-muted">{r.repo}{r.sections?.length ? ` · in “${[...new Set(r.sections)].slice(0, 2).join("”, “")}”` : ""}{r.weight ? ` · ${Math.round(r.weight * 100)}% similar` : ""}</div></li>))}</ul>}
    </Card>
  );
}

function Browse({ sources, repo, path, anchor, open, openConcept, onAdd }: { sources: WikiSource[]; repo: string | null; path: string | null; anchor: string | null; open: Open; openConcept: (t: string) => void; onAdd: () => void }) {
  const ask = useConfirm();
  const toast = useToast();
  const qc = useQueryClient();
  const repoId = repo ?? sources[0]?.repo_id ?? null;
  const [editing, setEditing] = useState<"edit" | "new" | null>(null);
  const tree = useQuery({ queryKey: ["wiki-tree", repoId], enabled: !!repoId, queryFn: () => api<WikiTreeNode[]>(`/wiki/tree?repo_id=${repoId}`) });
  const firstPage = (nodes: WikiTreeNode[]): string | null => { for (const n of nodes) { if (n.path) return n.path; const c = firstPage(n.children); if (c) return c; } return null; };
  const pagePath = path ?? (tree.data ? firstPage(tree.data) : null);
  const page = useQuery({ queryKey: ["wiki-page", repoId, pagePath], enabled: !!repoId && !!pagePath, queryFn: () => api<WikiPage>(`/wiki/page?repo_id=${repoId}&path=${encodeURIComponent(pagePath!)}`) });
  const del = useMutation({
    mutationFn: () => api(`/wiki/page?repo_id=${repoId}&path=${encodeURIComponent(pagePath!)}`, { method: "DELETE" }),
    onSuccess: () => { toast("Page deleted"); qc.invalidateQueries({ queryKey: ["wiki-overview"] }); qc.invalidateQueries({ queryKey: ["wiki-tree"] }); qc.removeQueries({ queryKey: ["wiki-page"] }); open(repoId!, ""); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  useEffect(() => { if (page.data && anchor) document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [page.data, anchor]);

  if (!sources.length) return (
    <Card className="mx-auto max-w-2xl text-center">
      <h2 className="text-lg font-semibold">Start your wiki</h2>
      <p className="mt-1 text-sm text-muted">Everything you add is split into sections, linked to your code and searchable. Pick where your documentation lives:</p>
      <div className="mt-4 grid gap-3 text-left sm:grid-cols-3">
        {[["A README or notes", "Upload .md / .txt files or paste text."], ["A web page", "Import a README from a GitHub link or any public page."], ["Confluence", "Connect with an API token and import a page, tree or space."]].map(([t, d]) => (
          <button key={t} onClick={onAdd} className="rounded-lg border border-line p-3 hover:border-brand hover:bg-indigo-50/40"><div className="text-sm font-medium">{t}</div><div className="mt-1 text-xs text-muted">{d}</div></button>))}
      </div>
      <p className="mt-4 text-xs text-muted">Repositories you index also show their README and docs here automatically.</p>
    </Card>);
  const p = page.data;
  const current = sources.find((x) => x.repo_id === repoId);
  const finish = (np: string) => { setEditing(null); if (np && np !== pagePath) open(repoId!, np); };
  return (
    <div>
    <PageSearch open={open} />
    <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)_280px]">
      <Card className="max-h-[40rem] overflow-auto p-3">
        <label className="mb-2 block text-xs font-medium text-muted">Source
          <select aria-label="Source" className="mt-1 h-8 w-full rounded-md border border-line bg-white px-2 text-sm font-normal text-ink" value={repoId ?? ""} onChange={(e) => open(e.target.value, "")}>
            {sources.map((s) => <option key={s.repo_id} value={s.repo_id}>{s.name} ({s.pages})</option>)}</select></label>
        <div className="mb-2"><NewPageButton source={current} onClick={() => setEditing("new")} /></div>
        {tree.isLoading ? <Spinner /> : <ul aria-label="Page tree">{tree.data?.map((n) => <TreeNode key={n.key} node={n} depth={0} selected={pagePath} onPick={(pp) => open(repoId!, pp)} />)}</ul>}
      </Card>
      <div className="min-w-0">
        {editing === "new" ? <Card><CardTitle>New page in {current?.name}</CardTitle><PageEditor repoId={repoId!} path={null} onDone={finish} /></Card> : editing === "edit" && p ? <Card><CardTitle>Edit {p.title}</CardTitle><PageEditor repoId={repoId!} path={p.path} onDone={finish} /></Card> : page.isLoading || (!p && !page.error) ? <Spinner /> : page.error ? <ErrorNote error={page.error} /> : p && (
          <Card>
            <nav aria-label="Breadcrumbs" className="mb-2 flex flex-wrap items-center gap-1 text-xs text-muted">{p.breadcrumbs.map((b, i) => <span key={i} className="flex items-center gap-1">{i > 0 && <ChevronRight className="h-3 w-3" />}{b.path ? <button className="hover:underline" onClick={() => open(p.repo_id, b.path!)}>{b.title}</button> : b.title}</span>)}</nav>
            <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge tone={ORIGIN_TONE[p.source.type] ?? "gray"}>{p.source.type}</Badge>
              {p.insights && p.insights.kind !== "general" && <Badge tone="brand">{p.insights.kind_label}</Badge>}
              {p.insights && <span>{p.insights.reading_minutes} min read</span>}
              {p.source.version != null && p.source.version > 0 && <span>version {p.source.version}</span>}
              {p.source.updated_at && <span>updated {timeAgo(p.source.updated_at)}</span>}
              {p.source.synced_at && <span>synced {timeAgo(p.source.synced_at)}</span>}
              {p.deletable && <EditButton onClick={() => setEditing("edit")} />}
              {p.deletable && <button className="ml-auto inline-flex items-center gap-1 text-red-600 hover:underline" onClick={async () => { if (await ask({ title: `Delete "${p.title}"?`, message: "The page is removed from search, the graph and the wiki. You can upload it again later.", confirmLabel: "Delete page", danger: true })) del.mutate(); }}><Trash2 className="h-3.5 w-3.5" />Delete page</button>}
              {!p.deletable && !p.managed_by && <span className="ml-auto" title="Part of a code repository's index">From repository {p.repo}</span>}
              {p.managed_by && <span className="ml-auto" title="This page is kept in sync with its source. Remove the source on the Sources tab to delete it.">Synced from {p.managed_by}</span>}
              {p.source.url && /^https?:\/\//.test(p.source.url) && <a className="inline-flex items-center gap-1 text-brand hover:underline" href={p.source.url} target="_blank" rel="noopener noreferrer">Open original<ExternalLink className="h-3 w-3" /></a>}
            </div>
            {!!p.unresolved_links.length && <p role="alert" className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">{p.unresolved_links.length} link(s) on this page point to pages that do not exist: {p.unresolved_links.map((l) => l.link).join(", ")}</p>}
            <Markdown text={p.markdown} onOpen={open} />
          </Card>)}
      </div>
      <div className="space-y-3">
        {p && (<>
          {p.insights && <AtAGlance i={p.insights} />}
          {p.outline.length > 1 && <Card className="p-4"><CardTitle>On this page</CardTitle><ul className="space-y-1 text-xs">{p.outline.map((o, i) => <li key={i}><button className="text-left text-brand hover:underline" onClick={() => document.getElementById(slug(o.title))?.scrollIntoView({ behavior: "smooth" })}>{o.title}</button></li>)}</ul></Card>}
          <RefList title="Linked from" items={p.backlinks} open={open} empty="No other page links here." />
          <RefList title="Links to" items={p.links_out} open={open} />
          {!!p.mentions_code.length && (
            <Card className="p-4"><CardTitle>Code it describes</CardTitle>
              <ul className="space-y-1.5 text-sm">{p.mentions_code.map((m) => <li key={m.id}><Link className="text-brand hover:underline" to={`/knowledge?tab=graph&node=${m.id}`}><code className="text-xs">{m.qualname || m.name}</code></Link><div className="text-xs text-muted">{m.repo}:{m.path}</div></li>)}</ul></Card>)}
          <RefList title="Related pages" items={p.related} open={open} />
          {!!p.concepts.length && <Card className="p-4"><CardTitle>Concepts</CardTitle><div className="flex flex-wrap gap-1.5">{p.concepts.map((c) => <button key={c} onClick={() => openConcept(c)} className="rounded-full border border-line px-2 py-0.5 text-xs hover:bg-slate-50">{c}</button>)}</div></Card>}
        </>)}
      </div>
    </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ ask
function AskWiki({ open }: { open: Open }) {
  const [q, setQ] = useState("");
  const ask = useMutation({ mutationFn: () => api<AskResult>("/wiki/ask", { method: "POST", json: { query: q } }) });
  const similar = useMutation({ mutationFn: () => api<SimilarAnswer[]>("/wiki/similar", { method: "POST", json: { query: q } }) });
  const submit = (e: FormEvent) => { e.preventDefault(); if (q.trim().length > 1) { similar.reset(); similar.mutate(); ask.mutate(); } };
  const a = ask.data;
  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="flex gap-2"><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask the wiki — e.g. What do we do when the profile endpoint returns 500?" aria-label="Question" />
        <Button disabled={ask.isPending}><Sparkles className="h-4 w-4" />Ask</Button></form>
      <ErrorNote error={ask.error} />
      <SimilarAnswers items={similar.data} />
      {a && (<>
        <Card><CardTitle right={<Badge tone={a.mode === "llm" ? "brand" : "gray"}>{a.mode === "llm" ? "AI answer" : a.mode === "extractive" ? "extracted passages" : "no results"}</Badge>}>Answer</CardTitle>
          <p className="whitespace-pre-wrap text-sm leading-6">{a.answer}</p>
          {!!a.connections.length && <div className="mt-4 border-t border-line pt-3 text-xs"><div className="mb-1 font-medium text-muted">Connected code and pages</div>
            <ul className="space-y-0.5">{a.connections.slice(0, 6).map((c, i) => <li key={i}><b>{c.from}</b> <span className="text-muted">{c.label}</span> <code>{c.to}</code></li>)}</ul></div>}
        </Card>
        {a.sources.map((s) => (
          <Card key={s.id} className="p-4"><div className="mb-1 flex flex-wrap items-center gap-2 text-sm"><span className="text-xs text-muted">{s.id}</span>
            {s.repo_id ? <button className="font-medium text-brand hover:underline" onClick={() => open(s.repo_id!, s.file)}>{s.title}</button> : <b>{s.title}</b>}
            <span className="text-xs text-muted">{s.repo}</span>{s.url && /^https?:\/\//.test(s.url) && <a className="text-xs text-brand hover:underline" href={s.url} target="_blank" rel="noopener noreferrer">original</a>}</div>
            <pre className="max-h-24 overflow-hidden whitespace-pre-wrap rounded bg-slate-50 p-2 font-mono text-xs">{s.snippet}</pre></Card>))}
      </>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ concepts
function Concepts({ initial, open }: { initial: string | null; open: Open }) {
  const [term, setTerm] = useState<string | null>(initial);
  const [filter, setFilter] = useState("");
  const list = useQuery({ queryKey: ["wiki-concepts"], queryFn: () => api<ConceptRow[]>("/wiki/concepts?limit=300") });
  const detail = useQuery({ queryKey: ["wiki-concept", term], enabled: !!term, retry: false, queryFn: () => api<ConceptDetail>(`/wiki/concepts/detail?term=${encodeURIComponent(term!)}`) });
  const shown = (list.data ?? []).filter((c) => c.term.toLowerCase().includes(filter.toLowerCase()));
  const d = detail.data;
  return (
    <div className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
      <Card className="max-h-[40rem] overflow-auto p-3">
        <Input aria-label="Filter concepts" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-2" />
        {list.isLoading ? <Spinner /> : !shown.length ? <Empty>No recurring terms yet. Concepts appear when several pages mention the same identifiers or headings.</Empty> : (
          <ul className="space-y-0.5">{shown.map((c) => (
            <li key={c.term}><button onClick={() => setTerm(c.term)} className={cn("flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-slate-50", term === c.term && "bg-indigo-50")}>
              <span className="truncate">{c.term}</span>{c.in_code && <Badge tone="brand">in code</Badge>}<span className="ml-auto text-xs text-muted">{c.docs} pages</span></button></li>))}</ul>)}
      </Card>
      <div className="min-w-0 space-y-3">
        {!term ? <Empty>Pick a concept to see where it is defined, where it is used, and the code behind it.</Empty> : detail.isLoading ? <Spinner /> : detail.error ? <ErrorNote error={detail.error} /> : d && (<>
          <Card><CardTitle right={<Badge>{d.documents} pages</Badge>}>{d.term}</CardTitle>
            <p className="text-sm leading-6">{d.summary || "No summary available."}</p>
            <p className="mt-1 text-xs text-muted">from {d.summary_source.repo} · {d.summary_source.section}</p>
            {!!d.code.length && <div className="mt-3 flex flex-wrap gap-2">{d.code.map((c) => <Link key={c.id} to={`/knowledge?tab=graph&node=${c.id}`} className="rounded-md border border-line px-2 py-1 text-xs hover:bg-slate-50"><code>{c.qualname}</code> <span className="text-muted">{c.repo}:{c.path}</span></Link>)}</div>}
            {!!d.related_terms.length && <div className="mt-3 flex flex-wrap gap-1.5">{d.related_terms.map((r) => <button key={r.term} onClick={() => setTerm(r.term)} className="rounded-full border border-line px-2 py-0.5 text-xs hover:bg-slate-50">{r.term}</button>)}</div>}
          </Card>
          {!!d.definitions.length && <Card><CardTitle>Defined in</CardTitle><ul className="space-y-2 text-sm">{d.definitions.map((m, i) => <li key={i}><button className="text-brand hover:underline" onClick={() => open(m.repo_id, m.path)}>{m.section}</button> <span className="text-xs text-muted">{m.repo} · {m.path}</span></li>)}</ul></Card>}
          <Card><CardTitle>Mentioned in</CardTitle>{!d.mentions.length ? <p className="text-sm text-muted">Only defined, never mentioned elsewhere.</p> : <ul className="space-y-2 text-sm">{d.mentions.map((m, i) => <li key={i}><button className="text-brand hover:underline" onClick={() => open(m.repo_id, m.path)}>{m.section}</button> <span className="text-xs text-muted">{m.repo} · {m.path}</span></li>)}</ul>}</Card>
        </>)}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ health
function Health({ open }: { open: Open }) {
  const [days, setDays] = useState(180);
  const h = useQuery({ queryKey: ["wiki-health", days], queryFn: () => api<WikiHealth>(`/wiki/health?stale_days=${days}&limit=25`) });
  const d = h.data;
  if (h.isLoading) return <Spinner />;
  if (!d) return <ErrorNote error={h.error} />;
  const s = d.summary;
  const page = (r: { repo_id: string; path: string; title: string }) => <button className="text-left text-brand hover:underline" onClick={() => open(r.repo_id, r.path)}>{r.title}</button>;
  const Section = ({ title, count, hint, children }: { title: string; count: number; hint: string; children: React.ReactNode }) => (
    <Card><CardTitle right={<Badge tone={count ? "amber" : "green"}>{count}</Badge>}>{title}</CardTitle><p className="mb-2 text-xs text-muted">{hint}</p>{count ? children : <p className="text-sm text-emerald-700">Nothing to fix.</p>}</Card>);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm"><label htmlFor="stale">Stale after</label>
        <select id="stale" className="h-8 rounded-md border border-line bg-white px-2" value={days} onChange={(e) => setDays(+e.target.value)}>{[90, 180, 365].map((n) => <option key={n} value={n}>{n} days</option>)}</select></div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Orphan pages" count={s.orphans} hint="Nothing links to these and no parent page contains them.">
          <ul className="space-y-1 text-sm">{d.orphans.map((o) => <li key={o.repo_id + o.path}>{page(o)} <span className="text-xs text-muted">{o.repo}</span></li>)}</ul></Section>
        <Section title="Broken links" count={s.broken_links} hint="Links to pages that do not exist (renamed, deleted or mistyped).">
          <ul className="space-y-1 text-sm">{d.broken_links.map((b, i) => <li key={i}>{page(b)} <span className="text-xs text-muted">→ <code>{b.link.replace(/^wiki:/, "")}</code></span></li>)}</ul></Section>
        <Section title="Stale pages" count={s.stale} hint={`Not updated in ${s.stale_days}+ days.`}>
          <ul className="space-y-1 text-sm">{d.stale.map((o) => <li key={o.repo_id + o.path}>{page(o)} <span className="text-xs text-muted">{o.age_days} days · {o.repo}</span></li>)}</ul></Section>
        <Section title="Near-duplicate pages" count={s.duplicates} hint="Pages with almost identical content — consider merging.">
          <ul className="space-y-1 text-sm">{d.duplicates.map((x, i) => <li key={i}>{page(x.a)} ↔ {page(x.b)} <span className="text-xs text-muted">{Math.round(x.similarity * 100)}%</span></li>)}</ul></Section>
        <Section title="Undocumented code" count={s.undocumented_code} hint="Public classes and functions no page mentions, most-connected first.">
          <ul className="space-y-1 text-sm">{d.undocumented_code.map((n) => <li key={n.id}><Link className="text-brand hover:underline" to={`/knowledge?tab=graph&node=${n.id}`}><code className="text-xs">{n.qualname}</code></Link> <span className="text-xs text-muted">{n.repo}:{n.path}</span></li>)}</ul></Section>
        <Section title="Possibly outdated references" count={s.outdated_references} hint="Docs name code that no longer exists.">
          <ul className="space-y-1 text-sm">{d.outdated_references.map((r, i) => <li key={i}><code className="text-xs">{r.term}</code> <button className="text-xs text-brand hover:underline" onClick={() => open(r.repo_id, r.path)}>{r.section || r.path}</button></li>)}</ul></Section>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ drift
function Drift({ open, review }: { open: Open; review: (proposalId: string) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [status, setStatus] = useState("open");
  const list = useQuery({ queryKey: ["drift", status], queryFn: () => api<DriftAlertRow[]>(`/wiki/drift?status=${status}`) });
  const dismiss = useMutation({
    mutationFn: (id: string) => api(`/wiki/drift/${id}/dismiss`, { method: "POST" }),
    onSuccess: () => { toast("Marked as reviewed"); qc.invalidateQueries({ queryKey: ["drift"] }); qc.invalidateQueries({ queryKey: ["drift-count"] }); },
  });
  const draft = useMutation({
    mutationFn: (id: string) => api<ProposalDetail>(`/wiki/proposals/from-drift/${id}`, { method: "POST" }),
    onSuccess: (p) => { qc.invalidateQueries({ queryKey: ["proposals"] }); review(p.id); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 text-sm">
        <p className="text-muted">Pages that describe code which has changed since they were written. Edit the page and the alert closes by itself.</p>
        <select aria-label="Show" className="ml-auto h-8 rounded-md border border-line bg-white px-2" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="open">Needs review</option><option value="dismissed">Reviewed, no change needed</option><option value="resolved">Updated</option></select>
      </div>
      {list.isLoading ? <Spinner /> : !list.data?.length ? <Empty>{status === "open" ? "Nothing to review. When a re-index finds changed code that a page mentions, it appears here." : "Nothing here."}</Empty> : (
        <ul className="space-y-3">{list.data.map((a) => (
          <li key={a.id}><Card className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <button className="font-medium text-brand hover:underline" onClick={() => open(a.doc_repo_id, a.doc_path)}>{a.doc_title}</button>
              <span className="text-xs text-muted">{a.doc_repo} · {a.doc_path}</span>
              <span className="ml-auto text-xs text-muted">{a.repo} {a.from_sha}…{a.to_sha} · {timeAgo(a.updated_at)}</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs"><span className="text-muted">Changed code it mentions:</span>
              {a.symbols.map((s) => <Link key={s.id} to={`/knowledge?tab=graph&node=${s.id}`} className="rounded border border-line px-1.5 py-0.5 hover:bg-slate-50"><code>{s.qualname}</code></Link>)}</div>
            {a.status === "open" && (
              <div className="mt-3 flex gap-2">
                <Button size="sm" disabled={draft.isPending} onClick={() => draft.mutate(a.id)}><FileEdit className="h-3.5 w-3.5" />Draft an update</Button>
                <Button size="sm" variant="outline" onClick={() => open(a.doc_repo_id, a.doc_path)}>Open page</Button>
                <Button size="sm" variant="ghost" disabled={dismiss.isPending} onClick={async () => { if (await ask({ title: "No update needed?", message: "The alert is closed. It comes back only if the same code changes again.", confirmLabel: "Mark reviewed" })) dismiss.mutate(a.id); }}>Mark reviewed</Button>
              </div>)}
          </Card></li>))}</ul>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ proposals
function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <p className="text-sm text-muted">No difference from the current page.</p>;
  return (
    <pre aria-label="Proposed changes" className="max-h-96 overflow-auto rounded-lg border border-line bg-slate-50 p-3 font-mono text-xs leading-5">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "bg-emerald-100 text-emerald-900" : l.startsWith("-") && !l.startsWith("---") ? "bg-red-100 text-red-900" : l.startsWith("@@") ? "text-indigo-700" : "text-slate-600"}>{l || " "}</div>))}
    </pre>
  );
}

function Proposals({ selectedId, open }: { selectedId: string | null; open: Open }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [status, setStatus] = useState("pending");
  const [sel, setSel] = useState<string | null>(selectedId);
  const [editing, setEditing] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["proposals", status], queryFn: () => api<ProposalRow[]>(`/wiki/proposals?status=${status}`) });
  const detail = useQuery({ queryKey: ["proposal", sel], enabled: !!sel, retry: false, queryFn: () => api<ProposalDetail>(`/wiki/proposals/${sel}`) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["proposals"] }); qc.invalidateQueries({ queryKey: ["proposal"] }); qc.invalidateQueries({ queryKey: ["drift"] }); qc.invalidateQueries({ queryKey: ["drift-count"] }); qc.invalidateQueries({ queryKey: ["wiki-overview"] }); qc.invalidateQueries({ queryKey: ["wiki-page"] }); };
  const save = useMutation({ mutationFn: () => api(`/wiki/proposals/${sel}`, { method: "PUT", json: { proposed: editing } }), onSuccess: () => { setEditing(null); refresh(); } });
  const decide = useMutation({
    mutationFn: (v: { approve: boolean; target: string }) => api<ProposalDetail>(`/wiki/proposals/${sel}/decision`, { method: "POST", json: v }),
    onSuccess: (p) => { toast(p.status === "applied" ? p.result : "Proposal rejected"); refresh(); },
    onError: (e) => { toast((e as Error).message, "error"); refresh(); },
  });
  const d = detail.data;
  return (
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <Card className="max-h-[40rem] overflow-auto p-3">
        <select aria-label="Show proposals" className="mb-2 h-8 w-full rounded-md border border-line bg-white px-2 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="pending">Waiting for a decision</option><option value="applied">Applied</option><option value="rejected">Rejected</option><option value="failed">Could not be applied</option></select>
        {list.isLoading ? <Spinner /> : !list.data?.length ? <Empty>{status === "pending" ? "No proposals. Draft one from a Drift alert, or from a confirmed investigation." : "Nothing here."}</Empty> : (
          <ul className="space-y-1">{list.data.map((p) => (
            <li key={p.id}><button onClick={() => { setSel(p.id); setEditing(null); }} className={cn("w-full rounded-md px-2 py-2 text-left text-sm hover:bg-slate-50", sel === p.id && "bg-indigo-50")}>
              <div className="flex items-center gap-2"><Badge tone={p.kind === "new" ? "green" : "blue"}>{p.kind === "new" ? "new page" : "update"}</Badge><span className="truncate font-medium">{p.title}</span></div>
              <div className="mt-0.5 truncate text-xs text-muted">{p.source === "drift" ? "code changed" : p.source === "incident" ? "from an incident" : p.source} · {timeAgo(p.created_at)}</div></button></li>))}</ul>)}
      </Card>
      <div className="min-w-0">
        {!sel ? <Empty>Select a proposal to review the change. Nothing is written until you approve it.</Empty> : detail.isLoading ? <Spinner /> : detail.error ? <ErrorNote error={detail.error} /> : d && (
          <Card>
            <CardTitle right={<Badge tone={d.status === "applied" ? "green" : d.status === "pending" ? "amber" : d.status === "failed" ? "red" : "gray"}>{d.status}</Badge>}>{d.title}</CardTitle>
            <p className="text-sm">{d.rationale}</p>
            <p className="mt-1 text-xs text-muted">{d.kind === "new" ? `New page ${d.doc_repo ? `in ${d.doc_repo}` : "in the Runbooks collection"}: ${d.doc_path}` : `${d.doc_repo} · ${d.doc_path}`} · drafted by {d.drafted_by === "llm" ? "an AI model" : d.drafted_by === "edited" ? "a person (edited)" : "a template"}</p>
            {d.result && <p role="status" className={`mt-2 text-sm ${d.status === "applied" ? "text-emerald-700" : "text-red-600"}`}>{d.result}</p>}
            <div className="mt-4">
              {editing !== null ? (<>
                <Textarea rows={16} aria-label="Edit proposed text" className="font-mono text-xs" value={editing} onChange={(e) => setEditing(e.target.value)} />
                <div className="mt-2 flex gap-2"><Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>Save draft</Button><Button size="sm" variant="outline" onClick={() => setEditing(null)}>Cancel</Button></div>
                <ErrorNote error={save.error} /></>
              ) : d.kind === "update" ? <DiffView diff={d.diff} /> : <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-slate-50 p-3 font-mono text-xs">{d.proposed}</pre>}
            </div>
            {d.status === "pending" && editing === null && (
              <div className="mt-4 flex flex-wrap items-center gap-2">
                {(d.apply_options ?? []).map((t) => (
                  <Button key={t} disabled={decide.isPending} onClick={async () => { if (await ask({ title: t === "confluence_comment" ? "Post this as a Confluence comment?" : d.kind === "new" ? "Create this page?" : "Apply this change?", message: t === "confluence_comment" ? "The page itself is not changed. The suggestion is added as a comment for its owners to act on." : "The page is updated now and re-indexed.", confirmLabel: t === "confluence_comment" ? "Post comment" : "Approve" })) decide.mutate({ approve: true, target: t }); }}>
                    <Check className="h-4 w-4" />{t === "confluence_comment" ? "Approve and comment in Confluence" : d.kind === "new" ? "Approve and create" : "Approve and apply"}</Button>))}
                {!(d.apply_options ?? []).length && <span className="text-xs text-muted">This page lives in a code repository: copy the proposed text into a pull request.</span>}
                <Button variant="outline" onClick={() => setEditing(d.proposed)}>Edit first</Button>
                <Button variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate({ approve: false, target: "auto" })}>Reject</Button>
                {d.doc_repo_id && d.kind === "update" && <Button variant="ghost" onClick={() => open(d.doc_repo_id, d.doc_path)}>Open page</Button>}
              </div>)}
          </Card>)}
      </div>
    </div>
  );
}
