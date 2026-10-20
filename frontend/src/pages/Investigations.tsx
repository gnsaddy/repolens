import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Label, PageHeader, Textarea, statusTone } from "@/components/ui";
import { api, type InvestigationSummary, type Repo } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { pct, timeAgo } from "@/lib/utils";

export default function Investigations() {
  const { user } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const preset = (useLocation().state as { query?: string } | null)?.query ?? "";
  const [query, setQuery] = useState(preset);
  const [repoIds, setRepoIds] = useState<string[]>([]);
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const list = useQuery({ queryKey: ["investigations"], queryFn: () => api<InvestigationSummary[]>("/investigations"), refetchInterval: 4000 });
  const start = useMutation({
    mutationFn: () => api<InvestigationSummary>("/investigations", { method: "POST", json: { query, repo_ids: repoIds } }),
    onSuccess: (i) => { qc.invalidateQueries({ queryKey: ["investigations"] }); nav(`/investigations/${i.id}`); },
  });
  const ready = repos.data?.filter((r) => r.status === "ready") ?? [];
  const toggle = (id: string) => setRepoIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <div>
      <PageHeader title="Investigations" subtitle="Root-cause analysis across your repositories, commits and past incidents." />
      {canWrite(user) && (
        <Card className="mb-6">
          <CardTitle>New investigation</CardTitle>
          <Label>Bug report, error message or stack trace</Label>
          <Textarea rows={6} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={"Device onboarding fails after yesterday's deployment…\n\nTraceback (most recent call last): …"} />
          <div className="mt-3"><Label>Repositories (none selected = search all)</Label>
            <div className="flex flex-wrap gap-2">{ready.map((r) => (
              <button key={r.id} type="button" onClick={() => toggle(r.id)} aria-pressed={repoIds.includes(r.id)}
                className={`rounded-full border px-3 py-1 text-xs ${repoIds.includes(r.id) ? "border-brand bg-indigo-50 text-brand" : "border-line text-muted hover:bg-slate-50"}`}>{r.name}</button>))}
              {!ready.length && <span className="text-xs text-muted">No indexed repositories yet — add one under Repositories.</span>}</div></div>
          <div className="mt-4 flex items-center gap-3">
            <Button onClick={() => start.mutate()} disabled={query.trim().length < 5 || start.isPending}>Investigate</Button>
            <ErrorNote error={start.error} />
          </div>
        </Card>)}
      <Card>
        <CardTitle>History</CardTitle>
        {!list.data?.length ? <Empty>No investigations yet.</Empty> : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-medium">Title</th><th className="font-medium">Status</th><th className="font-medium">Confidence</th><th className="font-medium">Jira</th><th className="text-right font-medium">Started</th></tr></thead>
            <tbody>{list.data.map((i) => (
              <tr key={i.id} className="border-t border-line">
                <td className="max-w-sm truncate py-2"><Link className="text-brand hover:underline" to={`/investigations/${i.id}`}>{i.title}</Link></td>
                <td><Badge tone={statusTone(i.status)}>{i.status}</Badge></td>
                <td>{i.status === "completed" ? pct(i.confidence) : "—"}</td>
                <td className="text-muted">{i.jira_issue_key ?? "—"}</td>
                <td className="text-right text-muted">{timeAgo(i.created_at)}</td>
              </tr>))}</tbody>
          </table>)}
      </Card>
    </div>
  );
}
