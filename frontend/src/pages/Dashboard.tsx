import { useQuery } from "@tanstack/react-query";
import { FolderGit2, ShieldCheck, Timer, Search } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useState, type FormEvent } from "react";
import { Badge, Button, Card, CardTitle, Empty, Input, PageHeader, Stat, statusTone } from "@/components/ui";
import { api, type Dashboard as D } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { pct, timeAgo } from "@/lib/utils";

export default function Dashboard() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const { data } = useQuery({ queryKey: ["dashboard"], queryFn: () => api<D>("/dashboard"), refetchInterval: 10_000 });

  function go(e: FormEvent) {
    e.preventDefault();
    if (q.trim().length >= 5) nav("/investigations", { state: { query: q } });
  }
  const max = Math.max(1, ...(data?.top_repositories.map((r) => r.count) ?? [1]));
  return (
    <div>
      <PageHeader title={`Welcome back${user?.name ? `, ${user.name}` : ""}`} subtitle="Evidence-backed repository intelligence for faster debugging." />
      <form onSubmit={go} className="mb-6 flex gap-2">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Describe a bug, paste a stack trace or ask about your codebase…" aria-label="Investigate" />
        <Button type="submit"><Search className="h-4 w-4" />Investigate</Button>
      </form>
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Repositories indexed" value={`${data?.indexed_repositories ?? "—"}`} hint={`${data?.repositories ?? 0} connected`} />
        <Stat label="Investigations completed" value={data?.completed ?? "—"} hint={`${data?.investigations ?? 0} total`} />
        <Stat label="Verified accuracy" value={pct(data?.verified_accuracy)} hint={data?.reviewed ? `${data.reviewed} human-reviewed` : "No reviews yet"} />
        <Stat label="Avg. investigation time" value={data?.avg_duration_s != null ? `${data.avg_duration_s}s` : "—"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardTitle right={<Link to="/investigations" className="text-xs text-brand hover:underline">View all</Link>}>Recent investigations</CardTitle>
          {!data?.recent.length ? <Empty>No investigations yet. Add a repository, then describe a bug above.</Empty> : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-medium">Issue / Query</th><th className="font-medium">Repository</th><th className="font-medium">Status</th><th className="text-right font-medium">Time</th></tr></thead>
              <tbody>{data.recent.map((i) => (
                <tr key={i.id} className="border-t border-line">
                  <td className="max-w-xs truncate py-2"><Link className="text-brand hover:underline" to={`/investigations/${i.id}`}>{i.title}</Link></td>
                  <td className="text-muted">{i.repo}</td>
                  <td><Badge tone={statusTone(i.status)}>{i.status}</Badge></td>
                  <td className="text-right text-muted">{timeAgo(i.created_at)}</td>
                </tr>))}</tbody>
            </table>)}
        </Card>
        <Card>
          <CardTitle>Top investigated repositories</CardTitle>
          {!data?.top_repositories.length ? <Empty>Nothing yet.</Empty> : (
            <ul className="space-y-3">{data.top_repositories.map((r) => (
              <li key={r.name} className="text-sm"><div className="mb-1 flex justify-between"><span>{r.name}</span><span className="text-muted">{r.count}</span></div>
                <div className="h-2 rounded-full bg-slate-100"><div className="h-full rounded-full bg-brand" style={{ width: `${(r.count / max) * 100}%` }} /></div></li>))}</ul>)}
          <div className="mt-5 flex gap-4 text-xs text-muted"><span className="flex items-center gap-1"><FolderGit2 className="h-3.5 w-3.5" />Index</span><span className="flex items-center gap-1"><ShieldCheck className="h-3.5 w-3.5" />Verify</span><span className="flex items-center gap-1"><Timer className="h-3.5 w-3.5" />Learn</span></div>
        </Card>
      </div>
    </div>
  );
}
