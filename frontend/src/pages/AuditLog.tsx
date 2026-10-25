import { useQuery } from "@tanstack/react-query";
import { Download, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Spinner } from "@/components/ui";
import { api, tokenStore, type AuditFacets, type AuditPage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

const PAGE = 50;
const sel = "h-9 rounded-md border border-line bg-white px-2 text-sm";

/** Who did what, and what was refused. Admins only. */
export default function AuditLog() {
  const { user } = useAuth();
  const [action, setAction] = useState("");
  const [allowed, setAllowed] = useState("");
  const [actor, setActor] = useState("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const qs = `action=${encodeURIComponent(action)}&allowed=${allowed}&actor=${actor}&q=${encodeURIComponent(q)}&limit=${PAGE}&offset=${page * PAGE}`;
  const facets = useQuery({ queryKey: ["audit-facets"], enabled: user?.role === "admin", queryFn: () => api<AuditFacets>("/audit/facets") });
  const log = useQuery({ queryKey: ["audit", qs], enabled: user?.role === "admin", queryFn: () => api<AuditPage>(`/audit?${qs}`) });
  const reset = (fn: () => void) => { fn(); setPage(0); };
  const download = async () => {
    const r = await fetch(`/api/audit/export.csv?action=${encodeURIComponent(action)}&allowed=${allowed}`, { headers: { Authorization: `Bearer ${tokenStore.get()}` } });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob()); a.download = "audit-log.csv"; a.click(); URL.revokeObjectURL(a.href);
  };

  if (user?.role !== "admin") return <Empty>Only workspace admins can read the audit log.</Empty>;
  const total = log.data?.total ?? 0;
  return (
    <div>
      <PageHeader title="Audit log" subtitle="Every sensitive action, who did it, and everything that was refused. Passwords and keys are never recorded."
        actions={<Button variant="outline" size="sm" onClick={download}><Download className="h-4 w-4" />Download CSV</Button>} />
      <Card className="mb-4 p-3">
        <form className="flex flex-wrap items-center gap-2" role="search" onSubmit={(e) => { e.preventDefault(); reset(() => setQ(text.trim())); }}>
          <select aria-label="Area" className={sel} value={action} onChange={(e) => reset(() => setAction(e.target.value))}><option value="">All areas</option>{facets.data?.groups.map((g) => <option key={g} value={g}>{g}</option>)}</select>
          <select aria-label="Outcome" className={sel} value={allowed} onChange={(e) => reset(() => setAllowed(e.target.value))}><option value="">Allowed and refused</option><option value="yes">Allowed</option><option value="no">Refused{facets.data?.denied ? ` (${facets.data.denied})` : ""}</option></select>
          <select aria-label="Person" className={sel} value={actor} onChange={(e) => reset(() => setActor(e.target.value))}><option value="">Everyone</option>{facets.data?.users.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)}</select>
          <Input aria-label="Search the log" className="min-w-48 flex-1" placeholder="Search actions and targets…" value={text} onChange={(e) => { setText(e.target.value); if (!e.target.value) reset(() => setQ("")); }} />
          <Button size="sm" variant="outline">Search</Button>
        </form>
      </Card>
      <ErrorNote error={log.error} />
      {log.isLoading ? <Spinner /> : !log.data?.items.length ? <Empty>Nothing matches.</Empty> : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm" aria-label="Audit events">
            <thead className="bg-slate-50 text-left text-xs text-muted"><tr><th className="px-3 py-2 font-medium">When</th><th className="font-medium">Who</th><th className="font-medium">Action</th><th className="font-medium">Target</th><th className="font-medium">Outcome</th></tr></thead>
            <tbody>{log.data.items.map((e) => (
              <tr key={e.id} className="border-t border-line align-top">
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted" title={new Date(e.at).toLocaleString()}>{timeAgo(e.at)}</td>
                <td className="py-2">{e.actor}</td>
                <td className="py-2"><code className="text-xs">{e.action}</code></td>
                <td className="max-w-xs py-2"><div className="truncate" title={e.target}>{e.target || "—"}</div>
                  {!e.allowed && e.detail.reason && <div className="text-xs text-red-700">{e.detail.reason}</div>}
                  {Object.keys(e.detail).filter((k) => k !== "reason").length > 0 && <details className="text-xs text-muted"><summary className="cursor-pointer">details</summary>{Object.entries(e.detail).filter(([k]) => k !== "reason").map(([k, v]) => <div key={k}>{k}: {v}</div>)}</details>}</td>
                <td className="py-2">{e.allowed ? <Badge tone="green">allowed</Badge> : <Badge tone="red"><ShieldAlert className="mr-1 inline h-3 w-3" />refused</Badge>}</td>
              </tr>))}</tbody>
          </table>
          <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
            <span>{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total.toLocaleString()}</span>
            <span className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</Button><Button size="sm" variant="outline" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Older</Button></span>
          </div>
        </Card>)}
    </div>
  );
}
