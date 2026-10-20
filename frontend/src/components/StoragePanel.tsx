import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDrive } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardTitle, ErrorNote } from "@/components/ui";
import { api, type CleanupResult, type StorageInfo } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

export const fmtBytes = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

/** Disk used by cloned repositories, and a button to free clones nobody has used lately (indexes are kept). */
export default function StoragePanel() {
  const qc = useQueryClient();
  const [days, setDays] = useState(30);
  const info = useQuery({ queryKey: ["storage"], queryFn: () => api<StorageInfo>("/repositories/storage") });
  const clean = useMutation({
    mutationFn: (dry_run: boolean) => api<CleanupResult>("/repositories/storage/cleanup", { method: "POST", json: { older_than_days: days, dry_run } }),
    onSuccess: (r) => { if (!r.dry_run) qc.invalidateQueries({ queryKey: ["storage"] }); },
  });
  const d = info.data;
  if (!d) return null;
  return (
    <Card>
      <CardTitle right={<Badge>{fmtBytes(d.total_bytes)}</Badge>}><span className="inline-flex items-center gap-1.5"><HardDrive className="h-4 w-4" />Clone storage</span></CardTitle>
      {!d.checkouts.length ? <p className="text-xs text-muted">No cloned repositories. Local-path repositories are read in place and use no extra space.</p> : (
        <ul className="space-y-1.5 text-xs">{d.checkouts.map((c) => (
          <li key={c.path} className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate" title={c.repos.join(", ")}>{c.repos.join(", ")}</span>
            {c.partial && <Badge tone="blue">blob-less</Badge>}{!c.exists && <Badge tone="amber">removed</Badge>}
            <span className="text-muted">{c.exists ? fmtBytes(c.bytes) : "—"} · {c.last_used_at ? timeAgo(c.last_used_at) : "never used"}</span></li>))}</ul>)}
      <div className="mt-3 flex items-center gap-2 text-xs">
        <label htmlFor="ttl">Unused for</label>
        <select id="ttl" className="h-8 rounded-md border border-line bg-white px-2" value={days} onChange={(e) => setDays(+e.target.value)}>{[0, 7, 30, 90].map((n) => <option key={n} value={n}>{n === 0 ? "any time" : `${n} days`}</option>)}</select>
        <Button size="sm" variant="outline" disabled={clean.isPending} onClick={() => clean.mutate(true)}>Preview</Button>
        <Button size="sm" disabled={clean.isPending} onClick={() => { if (confirm("Delete unused clones? Indexes stay; clones are re-created when re-indexing.")) clean.mutate(false); }}>Free space</Button>
      </div>
      <p className="mt-1 text-xs text-muted">Automatic cleanup after {d.ttl_days || "∞"} days unused. Deleted clones are re-created on the next index.</p>
      <ErrorNote error={clean.error} />
      {clean.data && <p role="status" className="mt-2 text-xs text-emerald-700">{clean.data.dry_run ? "Would free" : "Freed"} {fmtBytes(clean.data.freed_bytes)} from {clean.data.removed.length} clone(s).</p>}
    </Card>
  );
}
