import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useConfirm, useToast } from "@/components/feedback";
import Modal from "@/components/Modal";
import ForceGraph, { NODE_COLORS, REL_COLORS } from "@/components/ForceGraph";
import SystemGraph, { heatColor, TRUST_COLOR, type HeatMetric } from "@/components/SystemGraph";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Label, PageHeader, Spinner, Textarea } from "@/components/ui";
import { api, type ChangePair, type CodeGraph, type Impact, type LinkChanges, type LinkCoverage, type Repo, type Surface, type SystemAnalysis, type SystemGroup, type SystemLink, type SystemOverview } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";
import Markdown from "./wiki/Markdown";

const METRICS: [HeatMetric, string][] = [["none", "No colour"], ["commits", "Commits"], ["lines", "Lines changed"], ["fixes", "Fix commits"], ["drift_open", "Docs out of date"]];
const REL: Record<string, string> = { USES: "uses (imported from)", HTTP: "calls over HTTP", CALLS: "calls", IMPORTS: "imports", MENTIONS: "documents", RELATED_TO: "resembles", TESTS: "tests" };
/** What stands behind a link, in words: the proof, not just a colour. */
const proof = (rel: string, trust: string) =>
  rel === "HTTP" ? "URL matches a route" : rel === "USES" || rel === "IMPORTS" ? (trust === "fact" ? "proved by an import" : "import found, name matched inside the package")
    : trust === "fact" ? "found in the code" : "name match only";
const TRUST: Record<string, string> = { fact: "found in the code", inferred: "inferred", uncertain: "uncertain" };
const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const same = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join() === [...b].sort().join();

/** Pick repositories, see how they depend on each other and change together, save the picture as a named system, and
 *  write an overview of it that the whole workspace can read. */
export default function GraphPage() {
  const { user } = useAuth();
  const write = canWrite(user);
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [picked, setPicked] = useState<string[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [metric, setMetric] = useState<HeatMetric>("fixes");
  const [selected, setSelected] = useState<string | null>(null);
  const [link, setLink] = useState<SystemLink | null>(null);
  const [saving, setSaving] = useState<"new" | "update" | null>(null);
  const [weak, setWeak] = useState(false);
  const [node, setNode] = useState<string | null>(null);

  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => api<{ groups: SystemGroup[]; max_repos: number }>("/groups") });
  const active = groups.data?.groups.find((g) => g.id === activeId) ?? null;
  const key = [...picked].sort().join(",");
  const analysis = useQuery({ queryKey: ["group-analysis", key], enabled: picked.length > 0, queryFn: () => api<SystemAnalysis>(`/groups/analysis?repo_ids=${key}`) });
  const focus = link ? `&src=${link.src}&dst=${link.dst}` : "";
  const code = useQuery({ queryKey: ["code-graph", key, weak, focus], enabled: picked.length > 1, queryFn: () => api<CodeGraph>(`/groups/code-graph?repo_ids=${key}${focus}${weak ? "&weak=true" : ""}`) });
  const overviews = useQuery({ queryKey: ["group-overviews", activeId], enabled: !!activeId, queryFn: () => api<{ overviews: SystemOverview[]; hidden: number }>(`/groups/${activeId}/overviews`) });
  const names = useMemo(() => new Map((repos.data ?? []).map((r) => [r.id, r.name])), [repos.data]);
  const maxRepos = groups.data?.max_repos ?? 15;
  const dirty = !!active && !same(picked, active.repo_ids);

  useEffect(() => { setSelected(null); setLink(null); setNode(null); }, [key]);
  const open = (g: SystemGroup | null) => { setActiveId(g?.id ?? null); setPicked(g?.repo_ids ?? []); };
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < maxRepos ? [...p, id] : p));

  const refresh = () => qc.invalidateQueries({ queryKey: ["groups"] });
  const remove = useMutation({ mutationFn: (id: string) => api(`/groups/${id}`, { method: "DELETE" }), onSuccess: () => { open(null); refresh(); toast("System deleted"); } });
  const makeOverview = useMutation({
    mutationFn: () => api<SystemOverview>(`/groups/${activeId}/overview`, { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["group-overviews", activeId] }); toast("Overview written"); },
  });

  const a = analysis.data;
  const pairs = a ? new Map(a.repos.map((r) => [r.id, r.name])) : names;
  const shownLink = link ?? null;

  return (
    <div>
      <PageHeader title="Graph" subtitle="Pick repositories to see how they depend on each other, which change together, and where trouble concentrates."
        actions={<Link to="/knowledge?tab=graph" className="text-sm text-brand hover:underline">Open the code-level graph</Link>} />
      <div className="grid gap-4 lg:grid-cols-4">
        <div className="space-y-4 lg:col-span-1">
          <Card aria-label="Saved systems">
            <CardTitle right={active && <button className="text-xs text-muted hover:underline" onClick={() => open(null)}>Start a new selection</button>}>Saved systems</CardTitle>
            {groups.isLoading ? <Spinner /> : groups.error ? <ErrorNote error={groups.error} /> : !groups.data?.groups.length ? <p className="text-sm text-muted">None yet. Pick repositories below and save the picture.</p> : (
              <ul className="space-y-1">{groups.data.groups.map((g) => (
                <li key={g.id}><button onClick={() => open(g)} aria-pressed={g.id === activeId} className={`w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-slate-50 ${g.id === activeId ? "bg-indigo-50 font-medium text-brand" : ""}`}>
                  {g.name}<span className="block text-xs font-normal text-muted">{g.repo_ids.length + g.hidden} repositories · {g.created_by_name}</span></button></li>))}</ul>)}
          </Card>
          <Card aria-label="Repositories">
            <CardTitle>Repositories ({picked.length} of {maxRepos})</CardTitle>
            {repos.isLoading ? <Spinner /> : !repos.data?.length ? <p className="text-sm text-muted">Add repositories first.</p> : (
              <ul className="max-h-72 space-y-1 overflow-auto">{repos.data.map((r) => (
                <li key={r.id}><label className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-slate-50">
                  <input type="checkbox" checked={picked.includes(r.id)} onChange={() => toggle(r.id)} /><span className="min-w-0 flex-1 truncate">{r.name}</span>
                  {r.source === "docs" && <Badge tone="blue">docs</Badge>}</label></li>))}</ul>)}
            {write && picked.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {!active && <Button size="sm" onClick={() => setSaving("new")}><Save className="h-4 w-4" />Save as a system</Button>}
                {dirty && <Button size="sm" onClick={() => setSaving("update")}><Save className="h-4 w-4" />Update “{active!.name}”</Button>}
                {active && <Button size="sm" variant="outline" aria-label={`Delete ${active.name}`} onClick={async () => { if (await confirm({ title: "Delete this system?", message: `${active.name}. The repositories stay; the saved overviews are deleted.`, confirmLabel: "Delete", danger: true })) remove.mutate(active.id); }}><Trash2 className="h-4 w-4" />Delete</Button>}
              </div>)}
            <ErrorNote error={remove.error} />
          </Card>
        </div>

        <div className="space-y-4 lg:col-span-3">
          {!picked.length ? <Empty>Choose two or more repositories to see how they connect.</Empty> : analysis.isLoading ? <Spinner /> : !a ? <ErrorNote error={analysis.error} /> : (<>
            {picked.length > 1 && <CodeDependencies coverage={a.coverage} names={pairs} cg={code.data} loading={code.isLoading} error={code.error} weak={weak} onWeak={setWeak} node={node} onNode={setNode}
              focus={link ? `${pairs.get(link.src)} → ${pairs.get(link.dst)}` : null} onClear={() => { setLink(null); setSelected(null); }} />}
            {picked.length > 1 && <WhatChanged ids={key} names={pairs} />}
            <Card aria-label="Repository map">
              <CardTitle right={<label className="flex items-center gap-2 text-xs text-muted">Colour repositories by
                <select aria-label="Colour repositories by" value={metric} onChange={(e) => setMetric(e.target.value as HeatMetric)} className="h-8 rounded-md border border-line bg-white px-2 text-sm text-ink">{METRICS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>}>
                Repository map{active ? `: ${active.name}` : ""}</CardTitle>
              {picked.length < 2 ? <p className="mb-2 text-sm text-muted">Choose a second repository to see how they relate.</p> : <p className="mb-2 text-xs text-muted">Click an arrow to show only the code behind it in the graph above.</p>}
              <SystemGraph repos={a.repos} links={a.links} metric={metric} selected={selected} onRepo={(id) => { setSelected(selected === id ? null : id); setLink(null); }} onLink={(l) => { setLink(l); setSelected(`${l.src}>${l.dst}`); }} />
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                {(["fact", "inferred", "uncertain"] as const).map((t) => <span key={t} className="flex items-center gap-1"><i className="inline-block h-0.5 w-5" style={{ background: TRUST_COLOR[t] }} />{TRUST[t]}</span>)}
                <span>Arrow: depends on. Number: links the code proves, by an import or a URL (~ = matched by name only).</span>
                {metric !== "none" && <span className="flex items-center gap-1">less<i className="inline-block h-2.5 w-16 rounded" style={{ background: `linear-gradient(90deg, ${heatColor(0)}, ${heatColor(1)})` }} />more, over the last {a.window_days} days</span>}
              </div>
              {picked.length > 1 && !a.links.length && <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-muted">No links between these repositories were found in the code or documents. They may still be related through something this analysis cannot see, such as a shared database or a message queue.</p>}
              {shownLink && (
                <div className="mt-3 rounded-lg border border-line p-3 text-sm" aria-label="Link details">
                  <div className="font-medium">{pairs.get(shownLink.src)} → {pairs.get(shownLink.dst)} <Badge tone={shownLink.trust === "fact" ? "green" : "amber"}>{TRUST[shownLink.trust]}</Badge></div>
                  <div className="text-xs text-muted">{Object.entries(shownLink.rels).map(([k, n]) => `${REL[k] ?? k.toLowerCase()} ${n}`).join(", ")}{shownLink.weak > 0 && ` · ${shownLink.weak} matched by name only`}</div>
                  <ul className="mt-2 space-y-1 text-xs">{shownLink.examples.map((e, i) => <li key={i}><code>{e.from}</code> → <code>{e.to}</code>{e.evidence && <span className="text-muted"> — {e.evidence}</span>}</li>)}</ul>
                </div>)}
            </Card>

            <div className="grid gap-4 xl:grid-cols-2">
              <Card aria-label="Changes together">
                <CardTitle>Repositories that change together</CardTitle>
                {!a.coupling.length ? <p className="text-sm text-muted">None in the last {a.window_days} days. A shared ticket key in commit messages, or the same person changing two repositories on one day, counts.</p> : (
                  <ul className="divide-y divide-line text-sm">{a.coupling.slice(0, 8).map((c) => (
                    <li key={`${c.a}${c.b}`} className="py-1.5"><div className="font-medium">{pairs.get(c.a)} &amp; {pairs.get(c.b)}</div>
                      <div className="text-xs text-muted">{c.tickets ? `${c.tickets} shared ticket${c.tickets > 1 ? "s" : ""} (${c.ticket_examples.join(", ")}) · ` : ""}{c.days} day{c.days === 1 ? "" : "s"} the same person changed both</div></li>))}</ul>)}
              </Card>
              <Card aria-label="Where trouble concentrates">
                <CardTitle>Where change and trouble concentrate</CardTitle>
                <table className="w-full text-sm"><thead><tr className="text-left text-xs text-muted"><th className="py-1 font-normal">Repository</th><th className="font-normal">Commits</th><th className="font-normal">Lines</th><th className="font-normal">Fixes</th><th className="font-normal">Docs</th></tr></thead>
                  <tbody>{a.repos.map((r) => (
                    <tr key={r.id} className="border-t border-line"><td className="py-1.5 pr-2 font-medium">{r.name}</td>
                      {(["commits", "lines", "fixes", "drift_open"] as const).map((k) => <td key={k} className="pr-1"><span className="inline-block min-w-8 rounded px-1.5 py-0.5 text-center" style={{ background: heatColor(r.heat[k]) }}>{r.risk[k]}</span></td>)}</tr>))}</tbody></table>
                <p className="mt-2 text-xs text-muted">Last {a.window_days} days. Docs = pages that may be out of date.</p>
              </Card>
            </div>

            {picked.length > 1 && <PublicSurface repos={a.repos.map((r) => ({ id: r.id, name: r.name }))} />}
            <Card aria-label="Overview">
              <CardTitle right={write && active && (
                <Button size="sm" disabled={makeOverview.isPending || dirty} onClick={() => makeOverview.mutate()}><Sparkles className="h-4 w-4" />{makeOverview.isPending ? "Writing…" : "Create knowledge for these repositories"}</Button>)}>Overview</CardTitle>
              {!active ? <p className="text-sm text-muted">Save this selection as a system to write an overview of it. Everyone in the workspace can read it, and it shows who asked for it.</p> : (<>
                {write && <p className="mb-3 text-xs text-muted">Uses the language model once, about {tokens(a.estimate_tokens)} tokens. It is written from the facts above and nothing else.{dirty && " Update the system first so the overview matches what you see."}</p>}
                <ErrorNote error={makeOverview.error} />
                {overviews.isLoading ? <Spinner /> : !overviews.data?.overviews.length ? <p className="text-sm text-muted">No overview yet.</p> : (
                  <div className="space-y-4">{overviews.data.overviews.slice(0, 3).map((o, i) => (
                    <article key={o.id} aria-label={`Overview by ${o.created_by_name}`} className={i ? "border-t border-line pt-4" : ""}>
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted"><span>Created by <strong className="text-ink">{o.created_by_name}</strong> · {timeAgo(o.created_at)} · from {o.repositories} repositories</span>
                        {o.mode === "extractive" && <Badge tone="amber">plain list, no model</Badge>}
                        {o.partial && <Badge tone="gray">built from fewer repositories than the system has now</Badge>}</div>
                      {o.unsupported_names.length > 0 && <p role="status" className="mb-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">Check these names, they are not in the facts: {o.unsupported_names.join(", ")}</p>}
                      <Markdown text={o.content} onOpen={() => {}} />
                    </article>))}</div>)}
                {!!overviews.data?.hidden && <p className="mt-3 text-xs text-muted">{overviews.data.hidden} older overview{overviews.data.hidden > 1 ? "s were" : " was"} built from repositories you cannot access.</p>}
              </>)}
            </Card>
          </>)}
        </div>
      </div>
      {saving && <SaveSystem mode={saving} active={active} ids={picked} onClose={() => setSaving(null)}
        onSaved={(g) => { setSaving(null); setActiveId(g.id); setPicked(g.repo_ids); refresh(); toast(saving === "new" ? "System saved" : "System updated"); }} />}
    </div>
  );
}

function SaveSystem({ mode, active, ids, onClose, onSaved }: { mode: "new" | "update"; active: SystemGroup | null; ids: string[]; onClose: () => void; onSaved: (g: SystemGroup) => void }) {
  const [name, setName] = useState(mode === "update" ? active!.name : "");
  const [about, setAbout] = useState(mode === "update" ? active!.description : "");
  const save = useMutation({
    mutationFn: () => api<SystemGroup>(mode === "update" ? `/groups/${active!.id}` : "/groups", { method: mode === "update" ? "PUT" : "POST", json: { name: name.trim(), description: about.trim(), repo_ids: ids } }),
    onSuccess: onSaved,
  });
  return (
    <Modal title={mode === "update" ? "Update this system" : "Save as a system"} subtitle={`${ids.length} repositor${ids.length === 1 ? "y" : "ies"}. Everyone in the workspace can see it and who saved it.`} onClose={onClose}>
      <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); if (name.trim().length >= 2) save.mutate(); }}>
        <label className="block"><Label>Name</Label><Input aria-label="Name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Checkout" /></label>
        <label className="block"><Label>What it is (optional)</Label><Textarea aria-label="Description" rows={2} value={about} onChange={(e) => setAbout(e.target.value)} placeholder="The services behind an order" /></label>
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button disabled={name.trim().length < 2 || save.isPending}>{mode === "update" ? "Update" : "Save"}</Button></div>
      </form>
    </Modal>
  );
}

/** The classes, functions and files on both ends of each link, grouped by repository: the real dependency graph. */
function CodeDependencies({ cg, loading, error, weak, onWeak, node, onNode, focus, onClear, coverage, names }: {
  coverage?: Record<string, LinkCoverage>; names: Map<string, string>;
  cg?: CodeGraph; loading: boolean; error: unknown; weak: boolean; onWeak: (v: boolean) => void;
  node: string | null; onNode: (id: string | null) => void; focus: string | null; onClear: () => void;
}) {
  const byId = new Map((cg?.nodes ?? []).map((n) => [n.id, n]));
  const touching = (cg?.edges ?? []).filter((e) => node && (e.source === node || e.target === node) && e.rel !== "CONTAINS");
  const picked = node ? byId.get(node) : undefined;
  const unread = Object.entries(coverage ?? {}).flatMap(([repo, c]) =>
    Object.entries((c.unread ?? []).reduce<Record<string, number>>((acc, u) => ({ ...acc, [u.language]: (acc[u.language] ?? 0) + u.files }), {}))
      .map(([lang, n]) => `${lang} in ${names.get(repo) ?? repo} (${n} file${n === 1 ? "" : "s"})`));
  const label = (id: string) => { const n = byId.get(id); return n ? `${n.repo}: ${n.qualname || n.name}` : id; };
  const crossing = cg ? cg.edges.filter((e) => e.rel !== "CONTAINS" && byId.get(e.source)?.repo_id !== byId.get(e.target)?.repo_id).length : 0;
  return (
    <Card aria-label="Code dependencies">
      <CardTitle right={focus && <button className="text-xs text-brand hover:underline" onClick={onClear}>Show every pair</button>}>Code dependencies{focus ? `: ${focus}` : ""}</CardTitle>
      {loading ? <Spinner /> : error ? <ErrorNote error={error} /> : !cg ? null : (<>
        {!cg.nodes.length ? (
          <div className="rounded-lg bg-slate-50 p-4 text-sm text-muted">
            <p>No code-level link was found between these repositories{focus ? " in this direction" : ""}.</p>
            {cg.available.weak > 0 && !weak && <p className="mt-2">{cg.available.weak} calls match a name that exists in the other repository. Between unrelated projects these are usually coincidences, so they are hidden.</p>}
            <p className="mt-2">Imports are read for Python and JavaScript/TypeScript, and URLs are matched against the routes another repository serves. Dependencies through a shared database, a message queue or a URL held in configuration cannot be seen from the code.</p>
          </div>) : (
          <ForceGraph nodes={cg.nodes} edges={cg.edges} selectedId={node} onSelect={(id) => onNode(node === id ? null : id)} />)}
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
          {cg.nodes.length > 0 && <span>{crossing} link{crossing === 1 ? "" : "s"} across repositories, drawn amber and dashed between each repository's area.</span>}
          <span className="flex items-center gap-1"><i className="inline-block h-0.5 w-5" style={{ background: REL_COLORS.HTTP }} />calls over HTTP (URL matches a route)</span>
          <span className="flex items-center gap-1"><i className="inline-block h-0.5 w-5" style={{ background: REL_COLORS.USES }} />uses a class or function it imports (the import line is the proof)</span>
          <span className="flex items-center gap-1"><i className="inline-block h-0.5 w-5" style={{ background: REL_COLORS.CALLS }} />calls the code states</span>
          {(["function", "class", "file"] as const).map((t) => <span key={t} className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-full" style={{ background: NODE_COLORS[t] }} />{t}</span>)}
        </div>
        {(cg.available.weak > 0 || weak) && (
          <label className="mt-2 flex items-start gap-2 text-xs text-muted"><input type="checkbox" className="mt-0.5" checked={weak} onChange={(e) => onWeak(e.target.checked)} />
            <span>Also show the {cg.available.weak} calls matched by name only. They only say two repositories use the same word, and are often wrong.</span></label>)}
        {cg.truncated && <p className="mt-2 text-xs text-muted">Showing the strongest links. Click an arrow in the repository map to see one pair in full.</p>}
        {picked && (
          <div className="mt-3 rounded-lg border border-line p-3 text-sm" aria-label="Selected code">
            <div className="font-medium">{picked.qualname || picked.name} <Badge>{picked.type}</Badge> <Badge tone="blue">{picked.repo}</Badge></div>
            <div className="text-xs text-muted"><code>{picked.path}{picked.start_line ? `:${picked.start_line}` : ""}</code></div>
            {touching.length > 0 && <ul className="mt-2 space-y-1 text-xs">{touching.map((e) => (
              <li key={e.id}>{e.source === node ? "→" : "←"} <strong>{REL[e.rel] ?? e.rel.toLowerCase()}</strong> <code>{label(e.source === node ? e.target : e.source)}</code>
                {" "}<Badge tone={e.trust === "fact" ? "green" : "amber"}>{proof(e.rel, e.trust ?? "")}</Badge>
                {e.evidence && <span className="text-muted"> — {e.evidence}</span>}</li>))}</ul>}
            <BlastRadius id={picked.id} />
          </div>)}
        {unread.length > 0 && (
          <p className="mt-3 text-xs text-muted" role="note">Not read: {unread.join("; ")}. A repository using these may depend on others in ways this graph cannot show yet.</p>)}
      </>)}
    </Card>
  );
}

/** What breaks if this changes: everything in other repositories that depends on it, who changes those places, and the
 *  callers one step further in. */
function BlastRadius({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["impact", id], queryFn: () => api<Impact>(`/groups/impact?node_id=${id}`), retry: false });
  if (q.isLoading) return <p className="mt-3 text-xs text-muted">Looking for what depends on this…</p>;
  const d = q.data;
  if (!d) return <ErrorNote error={q.error} />;
  const places = d.direct.reduce((n, x) => n + 1 + x.more, 0);
  return (
    <div className="mt-3 border-t border-line pt-3 text-xs" aria-label="Blast radius">
      <div className="text-sm font-medium">{places ? `${places} place${places === 1 ? "" : "s"} in ${d.repos.length} other repositor${d.repos.length === 1 ? "y" : "ies"} depend on this` : "Nothing in other repositories depends on this"}</div>
      {d.repos.length > 0 && <div className="mt-1 text-muted">{d.repos.map((r) => `${r.repo}: ${r.uses}`).join(" · ")}</div>}
      {d.direct.length > 0 && <ul className="mt-2 space-y-1.5">{d.direct.map((x) => (
        <li key={x.link}><code>{x.repo}: {x.label}</code>{x.owners.length > 0 && <span className="text-muted"> — ask {x.owners.map((o) => `${o.name} (${o.commits})`).join(", ")}</span>}
          <div className="text-muted">{x.evidence}{x.more > 0 && ` · +${x.more} more`}</div></li>))}</ul>}
      {d.indirect.length > 0 && <div className="mt-2"><div className="font-medium">Also affected, one step further in</div>
        <ul className="mt-1 space-y-0.5">{d.indirect.map((x, i) => <li key={i}><code>{x.repo}: {x.label}</code> <span className="text-muted">calls {x.through.split(":").pop()}</span></li>)}</ul></div>}
      {d.hidden > 0 && <p className="mt-2 text-muted">{d.hidden} more in repositories you cannot access.</p>}
      <p className="mt-2 text-muted">{d.note}</p>
    </div>
  );
}

/** Dependencies that appeared or disappeared lately, with the line that proves each. */
function WhatChanged({ ids, names }: { ids: string; names: Map<string, string> }) {
  const q = useQuery({ queryKey: ["link-changes", ids], queryFn: () => api<LinkChanges>(`/groups/changes?repo_ids=${ids}&days=7`), retry: false });
  if (q.isLoading || !q.data) return q.error ? <ErrorNote error={q.error} /> : null;
  const c = q.data;
  const Pair = ({ p, sign }: { p: ChangePair; sign: string }) => (
    <li className="py-1.5"><div className="font-medium">{sign} {p.from} → {p.to} <Badge tone={sign === "+" ? "amber" : "gray"}>{p.count} link{p.count === 1 ? "" : "s"}</Badge></div>
      <ul className="mt-0.5 space-y-0.5 text-xs text-muted">{p.examples.map((e, i) => <li key={i}><code>{e.from}</code> → <code>{e.to}</code> — {e.evidence}</li>)}</ul></li>
  );
  return (
    <Card aria-label="What changed">
      <CardTitle right={<span className="text-xs text-muted">last {c.days} days · tracking since {timeAgo(c.tracking_since)}</span>}>What changed between these repositories</CardTitle>
      {!c.new.length && !c.removed.length ? <p className="text-sm text-muted">No dependency between {[...names.values()].slice(0, 4).join(", ")}{names.size > 4 ? " and others" : ""} appeared or went away in the last {c.days} days. Each index is compared with the one before, so a new dependency is listed here as soon as it is indexed.</p> : (
        <ul className="divide-y divide-line text-sm">{c.new.map((p) => <Pair key={`n${p.src}${p.dst}`} p={p} sign="+" />)}{c.removed.map((p) => <Pair key={`r${p.src}${p.dst}`} p={p} sign="−" />)}</ul>)}
    </Card>
  );
}

/** What other repositories use from one repository, most used first, and what its package offers that nobody uses. */
function PublicSurface({ repos }: { repos: { id: string; name: string }[] }) {
  const [repo, setRepo] = useState(repos[0]?.id ?? "");
  const id = repos.some((r) => r.id === repo) ? repo : repos[0]?.id ?? "";
  const q = useQuery({ queryKey: ["surface", id], enabled: !!id, queryFn: () => api<Surface>(`/groups/surface?repo_id=${id}`), retry: false });
  const s = q.data;
  return (
    <Card aria-label="Public surface">
      <CardTitle right={<label className="flex items-center gap-2 text-xs text-muted">Repository
        <select aria-label="Repository" value={id} onChange={(e) => setRepo(e.target.value)} className="h-8 rounded-md border border-line bg-white px-2 text-sm text-ink">{repos.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}>What other repositories use from it</CardTitle>
      {q.isLoading ? <Spinner /> : !s ? <ErrorNote error={q.error} /> : (<>
        {!s.symbols.length ? <p className="text-sm text-muted">No other repository uses anything from {s.repo.name} that RepoLens can see.</p> : (
          <table className="w-full text-sm"><thead><tr className="text-left text-xs text-muted"><th className="py-1 font-normal">Class or function</th><th className="font-normal">Used</th><th className="font-normal">By</th></tr></thead>
            <tbody>{s.symbols.slice(0, 15).map((x) => (
              <tr key={x.id} className="border-t border-line"><td className="py-1.5 pr-2"><div className="font-medium">{x.name}</div><div className="text-xs text-muted"><code>{x.path}</code></div></td>
                <td className="pr-2">{x.uses}×</td><td className="text-xs text-muted">{x.repos.join(", ")}</td></tr>))}</tbody></table>)}
        {s.exports > 0 && <p className="mt-3 text-xs text-muted">{s.exports} names exported by its package{s.unused_exports.length ? `; not used by any repository here: ${s.unused_exports.map((e) => e.name).join(", ")}` : "; all of them are used"}.</p>}
        {s.hidden > 0 && <p className="mt-1 text-xs text-muted">{s.hidden} more use it in repositories you cannot access.</p>}
        <p className="mt-2 text-xs text-muted">{s.note}</p>
      </>)}
    </Card>
  );
}
