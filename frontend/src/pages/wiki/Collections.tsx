import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileText, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Card, CardTitle, Empty, ErrorNote, Spinner, statusTone } from "@/components/ui";
import { useConfirm, useToast } from "@/components/feedback";
import { api, type DocRow, type WikiSource } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";

/** Every document collection with its pages, so a README or a whole collection can be removed in one click. */
export default function Collections({ sources, onChanged }: { sources: WikiSource[]; onChanged: () => void }) {
  const { user } = useAuth();
  const ask = useConfirm();
  const toast = useToast();
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const cols = sources.filter((s) => s.kind === "collection");
  const docs = useQuery({ queryKey: ["docs", openId], enabled: !!openId, queryFn: () => api<DocRow[]>(`/knowledge/collections/${openId}/documents`) });
  const done = (msg: string) => { toast(msg); qc.invalidateQueries({ queryKey: ["docs"] }); qc.invalidateQueries({ queryKey: ["repos"] }); onChanged(); };
  const delDoc = useMutation({ mutationFn: (v: { repo: string; id: string }) => api(`/knowledge/collections/${v.repo}/documents/${v.id}`, { method: "DELETE" }), onSuccess: () => done("Page deleted"), onError: (e) => toast((e as Error).message, "error") });
  const delCol = useMutation({ mutationFn: (id: string) => api(`/knowledge/collections/${id}`, { method: "DELETE" }), onSuccess: () => { setOpenId(null); done("Collection deleted"); }, onError: (e) => toast((e as Error).message, "error") });
  const writable = canWrite(user);

  return (
    <Card>
      <CardTitle>Your collections</CardTitle>
      {!cols.length ? <Empty>Nothing here yet. Add a README or notes below and they show up as a collection.</Empty> : (
        <ul className="divide-y divide-line">{cols.map((c) => (
          <li key={c.repo_id} className="py-2">
            <div className="flex items-center gap-2 text-sm">
              <button className="flex min-w-0 flex-1 items-center gap-1.5 text-left" aria-expanded={openId === c.repo_id} onClick={() => setOpenId(openId === c.repo_id ? null : c.repo_id)}>
                {openId === c.repo_id ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                <span className="truncate font-medium">{c.name}</span><span className="text-xs text-muted">{c.pages} pages</span></button>
              <Badge tone={statusTone(c.status)}>{c.status}</Badge>
              {user?.role === "admin" && c.origin !== "confluence" && (
                <button aria-label={`Delete collection ${c.name}`} title="Delete collection" className="text-muted hover:text-red-600"
                  onClick={async () => { if (await ask({ title: `Delete the collection "${c.name}"?`, message: `All ${c.pages} pages are removed from search, the graph and the wiki. This cannot be undone.`, confirmLabel: "Delete collection", danger: true })) delCol.mutate(c.repo_id); }}><Trash2 className="h-4 w-4" /></button>)}
            </div>
            {openId === c.repo_id && (
              <div className="mt-2 rounded-lg bg-slate-50 p-2">
                {docs.isLoading ? <Spinner /> : !docs.data?.length ? <p className="p-2 text-xs text-muted">No pages.</p> : (
                  <ul className="space-y-0.5">{docs.data.map((d) => (
                    <li key={d.id} className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-white">
                      <FileText className="h-3.5 w-3.5 shrink-0 text-muted" /><span className="truncate">{d.path}</span><span className="ml-auto text-xs text-muted">{(d.size / 1024).toFixed(1)} KB</span>
                      {writable && d.deletable !== false && (
                        <button aria-label={`Delete ${d.path}`} className="text-muted hover:text-red-600"
                          onClick={async () => { if (await ask({ title: `Delete ${d.path}?`, message: "The page is removed from search, the graph and the wiki.", confirmLabel: "Delete page", danger: true })) delDoc.mutate({ repo: c.repo_id, id: d.id }); }}><Trash2 className="h-3.5 w-3.5" /></button>)}
                      {d.deletable === false && <span className="text-xs text-muted" title="Synced from a connected source. Remove the source to delete it.">synced</span>}
                    </li>))}</ul>)}
              </div>)}
          </li>))}</ul>)}
      <ErrorNote error={delDoc.error || delCol.error} />
    </Card>
  );
}
