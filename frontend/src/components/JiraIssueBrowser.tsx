import { useMutation, useQuery } from "@tanstack/react-query";
import { MessageSquare, Microscope, Search, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, ErrorNote, Input, Spinner } from "@/components/ui";
import { api, type JiraBatch, type JiraInvestigateBatch, type JiraIssue } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

const sel = "h-9 rounded-md border border-line bg-white px-2 text-sm";
const TYPE_TONE: Record<string, string> = { Bug: "bg-red-50 text-red-700", Task: "bg-blue-50 text-blue-700", Story: "bg-emerald-50 text-emerald-700", Epic: "bg-violet-50 text-violet-700", Incident: "bg-amber-50 text-amber-800" };
const STATUS_TONE: Record<string, string> = { "To Do": "bg-slate-100 text-slate-700", "In Progress": "bg-blue-50 text-blue-700", Done: "bg-emerald-50 text-emerald-700" };
const PRIORITY_DOT: Record<string, string> = { Highest: "bg-red-600", High: "bg-orange-500", Medium: "bg-amber-400", Low: "bg-sky-400", Lowest: "bg-slate-300" };
export const MAX_BATCH = 10;

const quote = (v: string) => `"${v.replace(/["\\]/g, " ")}"`;
const initials = (name?: string) => (name ? name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase() : "");

/** Find tickets with simple filters (every type by default), pick one or several, then analyse or investigate them. */
export default function JiraIssueBrowser({ canInvestigate, onOpen, onAnalyzed, onInvestigated, compact = false }: {
  canInvestigate: boolean; onOpen: (key: string) => void; onAnalyzed: (r: JiraBatch) => void; onInvestigated: (r: JiraInvestigateBatch) => void; compact?: boolean;
}) {
  const [project, setProject] = useState("");
  const [status, setStatus] = useState("open");
  const [type, setType] = useState("");
  const [who, setWho] = useState("");
  const [text, setText] = useState("");
  const [limit, setLimit] = useState(25);
  const [advanced, setAdvanced] = useState(false);
  const [custom, setCustom] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const built = useMemo(() => {
    const parts = [project && `project = ${quote(project)}`, status === "open" ? "statusCategory != Done" : status === "done" ? "statusCategory = Done" : "", type && `issuetype = ${quote(type)}`,
      who === "me" ? "assignee = currentUser()" : who === "none" ? "assignee is EMPTY" : "", text.trim() && `text ~ ${quote(text.trim())}`].filter(Boolean);
    return `${parts.join(" AND ")} ORDER BY updated DESC`.trim();
  }, [project, status, type, who, text]);
  const jql = custom ?? built;
  const touch = (fn: () => void) => { setCustom(null); setPicked([]); fn(); };
  const projects = useQuery({ queryKey: ["jira-projects"], retry: false, queryFn: () => api<{ key: string; name: string }[]>("/jira/projects") });
  const types = useQuery({ queryKey: ["jira-types"], retry: false, queryFn: () => api<string[]>("/jira/issue-types") });
  const issues = useQuery({ queryKey: ["jira-issues", jql, limit], retry: false, queryFn: () => api<JiraIssue[]>(`/jira/issues?jql=${encodeURIComponent(jql)}&limit=${limit}`) });
  const analyze = useMutation({ mutationFn: () => api<JiraBatch>("/jira/analyze-batch", { method: "POST", json: { keys: picked } }), onSuccess: onAnalyzed });
  const investigate = useMutation({ mutationFn: () => api<JiraInvestigateBatch>("/jira/investigate-batch", { method: "POST", json: { keys: picked, repo_ids: [] } }), onSuccess: onInvestigated });
  const rows = Array.isArray(issues.data) ? issues.data : [];
  const toggle = (k: string) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : p.length >= MAX_BATCH ? p : [...p, k]));
  const allShown = rows.slice(0, MAX_BATCH).map((r) => r.key);

  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <select aria-label="Project" className={sel} value={project} onChange={(e) => touch(() => setProject(e.target.value))}><option value="">All projects</option>{(Array.isArray(projects.data) ? projects.data : []).map((p) => <option key={p.key} value={p.key}>{p.name} ({p.key})</option>)}</select>
        <select aria-label="Type" className={sel} value={type} onChange={(e) => touch(() => setType(e.target.value))}><option value="">All types</option>{(Array.isArray(types.data) ? types.data : []).map((t) => <option key={t} value={t}>{t}</option>)}</select>
        <select aria-label="Status" className={sel} value={status} onChange={(e) => touch(() => setStatus(e.target.value))}><option value="open">Open</option><option value="done">Done</option><option value="any">Any status</option></select>
        <select aria-label="Assignee" className={sel} value={who} onChange={(e) => touch(() => setWho(e.target.value))}><option value="">Anyone</option><option value="me">Assigned to me</option><option value="none">Unassigned</option></select>
        <div className="relative"><Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted" /><Input aria-label="Search text" className="pl-8" placeholder="Search tickets…" value={text} onChange={(e) => touch(() => setText(e.target.value))} /></div>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>{issues.isLoading ? "Searching…" : `${rows.length} ticket${rows.length === 1 ? "" : "s"}${rows.length === limit ? ` (showing the latest ${limit})` : ""}`}</span>
        <button className="text-brand hover:underline" aria-expanded={advanced} onClick={() => setAdvanced((v) => !v)}>{advanced ? "Hide JQL" : "Edit JQL"}</button>
        <label className="flex items-center gap-1">Show<select aria-label="How many" className="h-7 rounded border border-line bg-white px-1" value={limit} onChange={(e) => setLimit(+e.target.value)}>{[25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        {!compact && <Link className="ml-auto text-brand hover:underline" to={`/wiki?tab=sources&connector=jira&jql=${encodeURIComponent(jql)}`}>Import these as searchable history</Link>}
      </div>
      {advanced && <Input value={jql} onChange={(e) => setCustom(e.target.value)} aria-label="JQL" className="font-mono text-xs" />}
      <ErrorNote error={issues.error ?? analyze.error ?? investigate.error} />

      {picked.length > 0 && (
        <div role="toolbar" aria-label="Selected tickets" className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-brand/30 bg-indigo-50 px-3 py-2 text-sm shadow-sm">
          <b>{picked.length} selected</b><span className="text-xs text-muted">(up to {MAX_BATCH})</span>
          <Button size="sm" disabled={analyze.isPending} onClick={() => analyze.mutate()}><Sparkles className="h-3.5 w-3.5" />{analyze.isPending ? "Analysing…" : "Analyse together"}</Button>
          {canInvestigate && <Button size="sm" variant="outline" disabled={investigate.isPending} onClick={() => investigate.mutate()}><Microscope className="h-3.5 w-3.5" />{investigate.isPending ? "Starting…" : `Investigate ${picked.length === 1 ? "it" : `all ${picked.length}`}`}</Button>}
          <button className="ml-auto text-xs text-brand hover:underline" onClick={() => setPicked([])}>Clear</button>
        </div>)}

      {issues.isLoading ? <Spinner /> : !rows.length ? <p className="rounded-lg border border-dashed border-line p-8 text-center text-sm text-muted">No tickets match. Try “All types” or “Any status”.</p> : (
        <ul aria-label="Tickets" className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-white">
          <li className="flex items-center gap-3 bg-slate-50 px-3 py-1.5 text-xs text-muted">
            <input type="checkbox" aria-label="Select the first ten" checked={picked.length > 0 && allShown.every((k) => picked.includes(k))} onChange={(e) => setPicked(e.target.checked ? allShown : [])} />
            <span>Select tickets to analyse or investigate several at once</span>
          </li>
          {rows.map((i) => (
            <li key={i.key} className={cn("flex items-center gap-3 px-3 py-2.5 text-sm hover:bg-slate-50", picked.includes(i.key) && "bg-indigo-50/60")}>
              <input type="checkbox" aria-label={`Select ${i.key}`} checked={picked.includes(i.key)} disabled={!picked.includes(i.key) && picked.length >= MAX_BATCH} onChange={() => toggle(i.key)} />
              <span className={cn("shrink-0 rounded px-1.5 py-0.5 text-xs font-medium", TYPE_TONE[i.type] ?? "bg-slate-100 text-slate-700")}>{i.type}</span>
              <button className="min-w-0 flex-1 text-left" onClick={() => onOpen(i.key)} title="Read the ticket and analyse it">
                <span className="mr-2 font-medium text-ink">{i.key}</span><span className="text-slate-700 hover:text-brand">{i.summary}</span>
              </button>
              <span className="hidden shrink-0 items-center gap-1 text-xs text-muted md:flex" title={`Priority: ${i.priority || "none"}`}>{i.priority && <><i className={cn("h-2 w-2 rounded-full", PRIORITY_DOT[i.priority] ?? "bg-slate-300")} />{i.priority}</>}</span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-xs", STATUS_TONE[i.status_category ?? ""] ?? "bg-slate-100 text-slate-700")}>{i.status}</span>
              <span className="hidden w-6 shrink-0 sm:block" title={i.assignee || "Unassigned"}>{i.assignee ? <span className="grid h-6 w-6 place-items-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-700">{initials(i.assignee)}</span> : <span className="block h-6 w-6 rounded-full border border-dashed border-line" />}</span>
              <span className="hidden w-20 shrink-0 text-right text-xs text-muted lg:block">{i.updated ? timeAgo(i.updated) : ""}</span>
              {!!i.comments && <span className="hidden shrink-0 items-center gap-0.5 text-xs text-muted sm:flex" title={`${i.comments} comments`}><MessageSquare className="h-3.5 w-3.5" />{i.comments}</span>}
              <span className="w-16 shrink-0 text-right">{i.investigation_id ? <Link className="text-xs text-brand hover:underline" to={`/investigations/${i.investigation_id}`}>View RCA</Link> : <Badge tone="gray">no RCA</Badge>}</span>
            </li>))}
        </ul>)}
    </div>
  );
}
