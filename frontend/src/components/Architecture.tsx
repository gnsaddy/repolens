import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, ErrorNote, Label, Spinner, Textarea } from "@/components/ui";
import { api, type Architecture, type ExportedGraph } from "@/lib/api";

const save = (name: string, type: string, data: BlobPart) => {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
};

const MAP = "svg[aria-label='How the repositories depend on each other']";

/** The repository map as a file: Mermaid or JSON from the server, SVG or PNG from the drawing on the page. */
export function ExportBar({ ids }: { ids: string }) {
  const toast = useToast();
  const text = useMutation({
    mutationFn: (format: "mermaid" | "json") => api<ExportedGraph>(`/groups/export?repo_ids=${ids}&format=${format}`),
    onSuccess: (r) => save(r.filename, "text/plain", r.content),
    onError: () => toast("Could not export"),
  });
  const drawing = () => {
    const el = document.querySelector<SVGSVGElement>(MAP);
    if (!el) return null;
    const copy = el.cloneNode(true) as SVGSVGElement;
    copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    return { el, xml: new XMLSerializer().serializeToString(copy) };
  };
  const asSvg = () => { const d = drawing(); if (d) save("repolens-dependencies.svg", "image/svg+xml", d.xml); };
  const asPng = () => {
    const d = drawing();
    if (!d) return;
    const box = d.el.viewBox.baseVal;
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = box.width * 2; c.height = box.height * 2;
      const g = c.getContext("2d");
      if (!g) return;
      g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
      g.drawImage(img, 0, 0, c.width, c.height);
      c.toBlob((b) => b && save("repolens-dependencies.png", "image/png", b));
    };
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(d.xml)}`;
  };
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="Export">
      <span className="text-xs text-muted">Export</span>
      <Button size="sm" variant="outline" onClick={asSvg}><Download className="h-4 w-4" />SVG</Button>
      <Button size="sm" variant="outline" onClick={asPng}><Download className="h-4 w-4" />PNG</Button>
      <Button size="sm" variant="outline" disabled={text.isPending} onClick={() => text.mutate("mermaid")}><Download className="h-4 w-4" />Mermaid</Button>
      <Button size="sm" variant="outline" disabled={text.isPending} onClick={() => text.mutate("json")}><Download className="h-4 w-4" />JSON</Button>
    </div>
  );
}

/** Rules a saved system must keep ("orders must not depend on admin") and the loops it has, each with its proof. */
export default function ArchitectureCard({ groupId, repos, write }: { groupId: string; repos: { id: string; name: string }[]; write: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["architecture", groupId], queryFn: () => api<Architecture>(`/groups/${groupId}/architecture`), retry: false });
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const [note, setNote] = useState("");
  const done = () => qc.invalidateQueries({ queryKey: ["architecture", groupId] });
  const add = useMutation({
    mutationFn: () => api(`/groups/${groupId}/rules`, { method: "POST", json: { src: src || repos[0]?.id, dst: dst || repos[1]?.id, note } }),
    onSuccess: () => { setNote(""); done(); toast("Rule added"); },
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/groups/${groupId}/rules/${id}`, { method: "DELETE" }), onSuccess: () => { done(); toast("Rule removed"); } });
  const a = q.data;
  const select = "h-8 rounded-md border border-line bg-white px-2 text-sm text-ink";
  const bad = !!a && (a.broken > 0 || a.cycles.length > 0);
  return (
    <Card aria-label="Architecture rules">
      <CardTitle right={a && <Badge tone={bad ? "red" : "green"}>{bad ? `${a.broken} broken, ${a.cycles.length} loop${a.cycles.length === 1 ? "" : "s"}` : "all kept"}</Badge>}>Architecture rules</CardTitle>
      {q.isLoading ? <Spinner /> : !a ? <ErrorNote error={q.error} /> : (<>
        {!a.rules.length ? <p className="text-sm text-muted">No rules yet. A rule says one repository must not depend on another; it is checked against the dependencies RepoLens can prove.</p> : (
          <ul className="space-y-2 text-sm">{a.rules.map((r) => (
            <li key={r.id} className="rounded-lg border border-line p-2">
              <div className="flex items-center gap-2"><span className="font-medium">{r.src_name} must not depend on {r.dst_name}</span>
                <Badge tone={r.broken ? "red" : "green"}>{r.broken ? "broken" : "kept"}</Badge>
                {write && <Button size="sm" variant="ghost" aria-label={`Remove rule ${r.src_name} to ${r.dst_name}`} className="ml-auto" onClick={() => remove.mutate(r.id)}><Trash2 className="h-4 w-4" /></Button>}</div>
              {r.note && <p className="text-xs text-muted">{r.note}</p>}
              {r.violation && (
                <ul className="mt-1 space-y-1 text-xs">
                  {r.violation.examples.map((e, i) => <li key={i}><code>{e.from}</code> → <code>{e.to}</code>{e.evidence && <span className="text-muted"> — {e.evidence}</span>}</li>)}
                  <li className="text-muted">{r.violation.links} proven link{r.violation.links === 1 ? "" : "s"}, {r.violation.trust === "fact" ? "found in the code" : r.violation.trust}</li>
                </ul>)}
            </li>))}</ul>)}
        {a.hidden_rules > 0 && <p className="mt-2 text-xs text-muted">{a.hidden_rules} more rule{a.hidden_rules > 1 ? "s" : ""} about repositories you cannot access.</p>}
        {write && repos.length > 1 && (
          <form className="mt-3 space-y-2 border-t border-line pt-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <select aria-label="Repository that must not depend" value={src || repos[0].id} onChange={(e) => setSrc(e.target.value)} className={select}>{repos.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
              <span>must not depend on</span>
              <select aria-label="Repository it must not depend on" value={dst || repos[1].id} onChange={(e) => setDst(e.target.value)} className={select}>{repos.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
            </div>
            <Label>Why (optional)<Textarea value={note} maxLength={300} rows={2} onChange={(e) => setNote(e.target.value)} /></Label>
            <ErrorNote error={add.error} />
            <Button size="sm" type="submit" disabled={add.isPending}><Plus className="h-4 w-4" />Add rule</Button>
          </form>)}
        <h4 className="mt-4 text-sm font-medium">Loops</h4>
        {!a.cycles.length ? <p className="text-sm text-muted">No repositories depend on each other in a loop.</p> : (
          <ul className="mt-1 space-y-2 text-sm">{a.cycles.map((c, i) => (
            <li key={i} className="rounded-lg border border-line p-2"><div className="font-medium">{c.names.join(" ⇄ ")}</div>
              <ul className="mt-1 space-y-0.5 text-xs">{c.edges.map((e) => <li key={`${e.src}${e.dst}`}>{c.names[c.repos.indexOf(e.src)]} → {c.names[c.repos.indexOf(e.dst)]}{e.proof && <span className="text-muted"> — {e.proof}</span>}</li>)}</ul></li>))}</ul>)}
      </>)}
    </Card>
  );
}
