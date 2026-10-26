import { useQuery } from "@tanstack/react-query";
import { Compass } from "lucide-react";
import { Link } from "react-router-dom";
import { communityColor } from "@/components/ForceGraph";
import { Badge, Card, CardTitle, ErrorNote, Spinner } from "@/components/ui";
import { api, type GraphReport, type Trust } from "@/lib/api";

const trustTone = (t: Trust) => (t === "fact" ? "green" : t === "inferred" ? "amber" : "gray");
const rel = (r: string) => r.toLowerCase().replace(/_/g, " ");

/** A repository's front door, built from its graph: the areas that hang together, the hubs to change with care,
 *  surprising links between areas, and good first questions. */
export default function StartHere({ repoId }: { repoId: string }) {
  const q = useQuery({ queryKey: ["graph-report", repoId], enabled: !!repoId, queryFn: () => api<GraphReport>(`/graph/report?repo_id=${repoId}`) });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorNote error={q.error} />;
  const r = q.data;
  if (!r?.stats) return null; // an older server, or nothing to show: the page works without this panel
  const list = <T,>(x: T[] | undefined) => (Array.isArray(x) ? x : []);
  const communities = list(r.communities), hubs = list(r.hubs), surprising = list(r.surprising), questions = list(r.questions);
  return (
    <Card aria-label="Start here">
      <CardTitle right={<span className="text-xs text-muted">{r.stats.nodes} items · {r.stats.edges} connections · {r.stats.communities} areas</span>}><Compass className="mr-1.5 inline h-4 w-4" />Start here: {r.repo.name}</CardTitle>
      <div className="grid gap-5 lg:grid-cols-2">
        <section aria-label="Areas">
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">Areas that hang together</h3>
          {!communities.length ? <p className="text-sm text-muted">No clear areas yet; the graph is small or loosely connected.</p> : (
            <ul className="space-y-1.5 text-sm">{communities.map((c) => (
              <li key={c.id} className="flex items-start gap-2"><span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: communityColor(c.id) }} />
                <span><b>{c.label}</b> <span className="text-xs text-muted">{c.size} items, {c.files} files</span>
                  <span className="block text-xs text-muted">{list(c.top).map((t) => t.name).join(" · ")}</span></span></li>))}</ul>)}
        </section>
        <section aria-label="Hubs">
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">Hubs: change with care</h3>
          <ul className="space-y-1 text-sm">{hubs.slice(0, 8).map((h) => (
            <li key={h.id} className="flex items-center gap-2"><Link className="truncate text-brand hover:underline" to={`/knowledge?tab=graph&node=${h.id}`}><code className="text-xs">{h.name}</code></Link>
              <span className="text-xs text-muted">{h.degree} links</span></li>))}</ul>
        </section>
      </div>
      {!!surprising.length && (
        <section aria-label="Surprising connections" className="mt-5">
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">Surprising connections between areas</h3>
          <ul className="space-y-1 text-sm">{surprising.map((s, i) => (
            <li key={i} className="flex flex-wrap items-center gap-1.5"><code className="text-xs">{s.source.name}</code><span className="text-xs text-muted">({s.from_area}) {rel(s.relation)}</span><code className="text-xs">{s.target.name}</code><span className="text-xs text-muted">({s.to_area})</span><Badge tone={trustTone(s.trust)}>{s.trust}</Badge></li>))}</ul>
        </section>)}
      {!!questions.length && (
        <section aria-label="Good first questions" className="mt-5">
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">Good first questions</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-sm">{questions.map((x) => <li key={x}>{x}</li>)}</ul>
        </section>)}
    </Card>
  );
}
