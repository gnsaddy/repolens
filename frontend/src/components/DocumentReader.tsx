import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, Download, ExternalLink, Maximize2, Minimize2, Minus, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Markdown from "@/pages/wiki/Markdown";
import { Badge, Button, ErrorNote, Input, Spinner } from "@/components/ui";
import { api, type WikiPage } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PAGED_FROM, splitSections } from "@/lib/sections";

export { splitSections };

const SIZES = [14, 16, 18, 20, 22];

/** A document, big enough to read: an outline of its sections, one section at a time or all of it, find in the document, text size, full screen. */
export default function DocumentReader({ repoId, path, onClose }: { repoId: string; path: string; onClose: () => void }) {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["reader", repoId, path], queryFn: () => api<WikiPage>(`/wiki/page?repo_id=${encodeURIComponent(repoId)}&path=${encodeURIComponent(path)}`) });
  const sections = useMemo(() => splitSections(q.data?.markdown ?? ""), [q.data?.markdown]);
  const [idx, setIdx] = useState(0);
  const [all, setAll] = useState<boolean | null>(null);
  const [find, setFind] = useState("");
  const [size, setSize] = useState(1);
  const [full, setFull] = useState(false);
  const showAll = all ?? sections.length <= PAGED_FROM;
  const current = Math.min(idx, sections.length - 1);

  useEffect(() => { setIdx(0); setAll(null); }, [repoId, path]);
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [full]);

  const matches = useMemo(() => {
    const term = find.trim().toLowerCase();
    if (term.length < 2) return [];
    return sections.flatMap((s, i) => {
      const hay = s.text.toLowerCase();
      const first = hay.indexOf(term);
      if (first < 0) return [];
      const count = hay.split(term).length - 1;
      const from = Math.max(0, first - 50);
      return [{ i, title: s.title, count, snippet: `${from ? "…" : ""}${s.text.slice(from, first + term.length + 90).replace(/\s+/g, " ")}…` }];
    });
  }, [find, sections]);

  const jump = (i: number) => { setAll(false); setIdx(i); };
  const download = () => {
    if (!q.data) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([q.data.markdown], { type: "text/markdown" }));
    a.download = path.split("/").pop() || "document.md"; a.click(); URL.revokeObjectURL(a.href);
  };
  const page = q.data;
  const kb = page ? Math.max(1, Math.round(page.markdown.length / 1024)) : 0;

  const shell = full ? "fixed inset-0 z-50 flex flex-col gap-3 bg-white p-3 text-ink" : "flex flex-col gap-3";
  return (
    <div className={shell} role={full ? "dialog" : undefined} aria-label={full ? "Document, full screen" : undefined}>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={onClose}><ArrowLeft className="h-4 w-4" />All documents</Button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-semibold" title={path}>{page?.title || path}</h2>
          <p className="truncate text-xs text-muted">{path}{page ? ` · ${sections.length} section${sections.length === 1 ? "" : "s"} · ${kb} KB` : ""}</p>
        </div>
        {page && <Badge tone="gray">{page.source.type}</Badge>}
        <div role="group" aria-label="Text size" className="flex items-center gap-1">
          <button className="grid h-8 w-8 place-items-center rounded-md border border-line hover:bg-slate-50" aria-label="Smaller text" disabled={size === 0} onClick={() => setSize((s) => Math.max(0, s - 1))}><Minus className="h-3.5 w-3.5" /></button>
          <span className="w-9 text-center text-xs tabular-nums text-muted" aria-label="Current text size">{SIZES[size]}px</span>
          <button className="grid h-8 w-8 place-items-center rounded-md border border-line hover:bg-slate-50" aria-label="Larger text" disabled={size === SIZES.length - 1} onClick={() => setSize((s) => Math.min(SIZES.length - 1, s + 1))}><Plus className="h-3.5 w-3.5" /></button>
        </div>
        <Button size="sm" variant="outline" onClick={download} disabled={!page}><Download className="h-4 w-4" />Download</Button>
        {page && <Link className="inline-flex h-8 items-center gap-1 rounded-md border border-line px-3 text-sm hover:bg-slate-50" to={`/wiki?tab=browse&repo=${encodeURIComponent(repoId)}&path=${encodeURIComponent(path)}`}><ExternalLink className="h-3.5 w-3.5" />Open in wiki</Link>}
        <Button size="sm" variant="outline" aria-pressed={full} onClick={() => setFull((f) => !f)}>{full ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}{full ? "Exit full screen" : "Full screen"}</Button>
      </div>

      {q.isLoading ? <Spinner /> : q.error ? <ErrorNote error={q.error} /> : !page ? null : (
        <div className={cn("grid min-h-0 gap-3 rounded-xl border border-line", full ? "flex-1 grid-rows-[minmax(0,1fr)]" : "h-[78vh] grid-rows-[minmax(0,1fr)]", "lg:grid-cols-[280px_minmax(0,1fr)]")}>
          <aside className="flex min-h-0 flex-col border-b border-line p-3 lg:border-b-0 lg:border-r" aria-label="Contents">
            <label className="relative block"><Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-muted" />
              <Input aria-label="Find in document" className="pl-8" placeholder="Find in this document…" value={find} onChange={(e) => setFind(e.target.value)} /></label>
            <div className="mt-2 flex items-center gap-2 text-xs">
              <label className="flex items-center gap-1.5"><input type="checkbox" checked={showAll} onChange={(e) => setAll(e.target.checked)} />Show all sections together</label>
            </div>
            <nav className="mt-2 min-h-0 flex-1 overflow-auto" aria-label={find.trim().length >= 2 ? "Search results" : "Sections"}>
              {find.trim().length >= 2 ? (
                matches.length ? (
                  <ul className="space-y-1.5 text-xs">
                    <li className="text-muted">{matches.reduce((n, m) => n + m.count, 0)} match{matches.reduce((n, m) => n + m.count, 0) === 1 ? "" : "es"} in {matches.length} section{matches.length === 1 ? "" : "s"}</li>
                    {matches.slice(0, 60).map((m) => (
                      <li key={m.i}><button className="w-full rounded-md p-1.5 text-left hover:bg-slate-50" onClick={() => jump(m.i)}>
                        <span className="font-medium">{m.title}</span> <span className="text-muted">({m.count})</span><span className="mt-0.5 block text-muted">{m.snippet}</span></button></li>))}
                  </ul>) : <p className="text-xs text-muted">Nothing in this document matches “{find.trim()}”.</p>
              ) : (
                <ol className="space-y-0.5 text-sm">
                  {sections.map((s, i) => (
                    <li key={i}><button aria-current={!showAll && i === current ? "true" : undefined} onClick={() => jump(i)}
                      className={cn("w-full truncate rounded-md px-2 py-1 text-left hover:bg-slate-50", !showAll && i === current && "bg-indigo-50 font-medium text-brand")}>{s.title}</button></li>))}
                </ol>)}
            </nav>
          </aside>
          <article className="reader min-h-0 overflow-auto p-5" style={{ "--reader-size": `${SIZES[size]}px` } as React.CSSProperties} aria-label="Document text">
            <div className="mx-auto max-w-3xl">
              {showAll
                ? <Markdown text={page.markdown} onOpen={(repo, p) => nav(`/wiki?tab=browse&repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(p)}`)} />
                : (<>
                  <Markdown key={current} text={sections[current].text} onOpen={(repo, p) => nav(`/wiki?tab=browse&repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(p)}`)} />
                  <div className="mt-8 flex items-center justify-between border-t border-line pt-3 text-sm">
                    <Button size="sm" variant="outline" disabled={current === 0} onClick={() => setIdx(current - 1)}><ChevronLeft className="h-4 w-4" />Previous</Button>
                    <span className="text-xs text-muted" aria-label="Position">{current + 1} of {sections.length}</span>
                    <Button size="sm" variant="outline" disabled={current >= sections.length - 1} onClick={() => setIdx(current + 1)}>Next<ChevronRight className="h-4 w-4" /></Button>
                  </div></>)}
            </div>
          </article>
        </div>)}
    </div>
  );
}
