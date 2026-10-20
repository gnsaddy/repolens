import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Users } from "lucide-react";
import { useState } from "react";
import { Badge, Card, CardTitle, Empty, ErrorNote, Input, PageHeader, Spinner, Stat } from "@/components/ui";
import { api, type OwnershipOverview, type PathOwners, type Repo } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

const COLORS = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899"];
const RISK = { ok: ["green", "Healthy"], single_owner: ["amber", "One person knows this"], stale_owner: ["red", "Owner inactive"] } as const;

function ShareBar({ owners }: { owners: { author: string; share: number }[] }) {
  return (
    <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100" role="img" aria-label={owners.map((o) => `${o.author} ${Math.round(o.share * 100)}%`).join(", ")}>
      {owners.map((o, i) => <div key={o.author} style={{ width: `${o.share * 100}%`, background: COLORS[i % COLORS.length] }} title={`${o.author} ${Math.round(o.share * 100)}%`} />)}
    </div>
  );
}

/** Who maintains what, and where knowledge sits with a single person. Built from commit history. */
export default function Ownership() {
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const code = (repos.data ?? []).filter((r) => r.source !== "docs" && r.status === "ready");
  const [sel, setSel] = useState("");
  const repoId = sel || code[0]?.id || "";
  const data = useQuery({ queryKey: ["ownership", repoId], enabled: !!repoId, queryFn: () => api<OwnershipOverview>(`/ownership/overview?repo_id=${repoId}`) });
  const [path, setPath] = useState("");
  const lookup = useQuery({ queryKey: ["ownership-path", repoId, path], enabled: !!repoId && path.trim().length > 1, queryFn: () => api<PathOwners>(`/ownership/path?repo_id=${repoId}&path=${encodeURIComponent(path.trim())}`) });
  const d = data.data;

  return (
    <div>
      <PageHeader title="Ownership" subtitle="Who maintains which code, and who to ask. Based on recent commit history, so a hint rather than a verdict." />
      {!code.length ? <Empty>Index a code repository first. Ownership is computed from its commit history.</Empty> : (<>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select aria-label="Repository" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={repoId} onChange={(e) => setSel(e.target.value)}>
            {code.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
          <div className="ml-auto w-72"><Input aria-label="Look up a file or folder" placeholder="Who owns… e.g. src/billing" value={path} onChange={(e) => setPath(e.target.value)} /></div>
        </div>
        {path.trim().length > 1 && (
          <Card className="mb-4">
            <CardTitle>Owners of <code className="text-xs">{path.trim()}</code></CardTitle>
            {lookup.isLoading ? <Spinner /> : !lookup.data?.owners.length ? <p className="text-sm text-muted">No commit history for that path.</p> : (
              <ul className="space-y-2 text-sm">{lookup.data.owners.map((o) => (
                <li key={o.author} className="flex items-center gap-3"><span className="w-40 truncate font-medium">{o.author}</span><span className="w-12 text-muted">{Math.round(o.share * 100)}%</span>
                  <span className="text-xs text-muted">{o.commits} commits · last {timeAgo(o.last_commit_at)}</span></li>))}</ul>)}
          </Card>)}
        {data.isLoading ? <Spinner /> : data.error ? <ErrorNote error={data.error} /> : d && (<>
          <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="People" value={d.summary.people} hint="with indexed commits" />
            <Stat label="Areas" value={d.summary.areas} hint="top-level folders" />
            <Stat label="Repo bus factor" value={d.summary.bus_factor} hint="people holding 80% of the knowledge" />
            <Stat label="Areas at risk" value={d.summary.areas_at_risk} hint="single owner or inactive owner" />
          </div>
          <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
            <Card>
              <CardTitle>Areas</CardTitle>
              {!d.areas.length ? <Empty>No commits indexed yet.</Empty> : (
                <ul className="divide-y divide-line">{d.areas.map((a) => (
                  <li key={a.area} className="py-3">
                    <div className="mb-1.5 flex flex-wrap items-center gap-2 text-sm"><code className="font-medium">{a.area}</code>
                      <Badge tone={RISK[a.risk][0]}>{a.risk !== "ok" && <AlertTriangle className="mr-1 h-3 w-3" />}{RISK[a.risk][1]}</Badge>
                      <span className="ml-auto text-xs text-muted">{a.commits} commits · active {timeAgo(a.last_activity)}</span></div>
                    <ShareBar owners={a.owners} />
                    <div className="mt-1.5 flex flex-wrap gap-x-4 text-xs text-muted">{a.owners.map((o, i) => <span key={o.author}><span className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />{o.author} {Math.round(o.share * 100)}%</span>)}</div>
                  </li>))}</ul>)}
            </Card>
            <Card>
              <CardTitle right={<Users className="h-4 w-4 text-muted" />}>People</CardTitle>
              <ul className="space-y-3 text-sm">{d.people.map((p) => (
                <li key={p.author}><div className="flex justify-between"><span className="font-medium">{p.author}</span><span className="text-xs text-muted">{Math.round(p.share * 100)}% · {p.commits} commits</span></div>
                  <div className="text-xs text-muted">{p.areas.join(", ")} · last {timeAgo(p.last_commit_at)}</div></li>))}</ul>
            </Card>
          </div>
          <p className="mt-3 text-xs text-muted">Recent work counts more than old work (half-life 6 months), bigger changes count more, and bots are ignored. It only sees indexed commits.</p>
        </>)}
      </>)}
    </div>
  );
}
