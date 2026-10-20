import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type GraphNode, type Repo } from "@/lib/api";

export const PAGES = [
  { label: "Dashboard", to: "/", hint: "overview" },
  { label: "Investigations", to: "/investigations", hint: "find the root cause of a bug" },
  { label: "Knowledge: ask & search", to: "/knowledge", hint: "ask questions over code and docs" },
  { label: "Knowledge: graph", to: "/knowledge?tab=graph", hint: "explore connections and heat map" },
  { label: "DeepMind Wiki: browse", to: "/wiki", hint: "documentation" },
  { label: "DeepMind Wiki: add content", to: "/wiki?tab=sources", hint: "README, text, URL, Confluence" },
  { label: "DeepMind Wiki: health", to: "/wiki?tab=health", hint: "orphans, broken links, stale pages" },
  { label: "Change risk", to: "/risk", hint: "score a commit or branch" },
  { label: "Repositories", to: "/repositories", hint: "add a repository, choose branches" },
  { label: "Jira", to: "/jira" },
  { label: "Token usage", to: "/usage", hint: "cost and budgets" },
  { label: "Settings", to: "/settings", hint: "LLM provider, GitHub" },
];

/** Ctrl/Cmd+K: jump to a page, a repository or a symbol without hunting through the menu. */
export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories"), enabled: open });
  const nodes = useQuery({ queryKey: ["palette-nodes", q], enabled: open && q.trim().length > 1, queryFn: () => api<GraphNode[]>(`/graph/nodes?q=${encodeURIComponent(q.trim())}&limit=6`) });
  useEffect(() => { if (open) { setQ(""); setIdx(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);

  const items = useMemo(() => {
    const s = q.trim().toLowerCase();
    const pages = PAGES.filter((p) => !s || `${p.label} ${p.hint ?? ""}`.toLowerCase().includes(s)).map((p) => ({ key: p.to, label: p.label, hint: p.hint ?? "page", to: p.to }));
    const rs = (repos.data ?? []).filter((r) => s && r.name.toLowerCase().includes(s)).map((r) => ({ key: `r-${r.id}`, label: r.name, hint: r.source === "docs" ? "documentation" : "repository", to: r.source === "docs" ? "/wiki" : "/repositories" }));
    const ns = (nodes.data ?? []).map((n) => ({ key: `n-${n.id}`, label: n.qualname || n.name, hint: `${n.type} · ${n.repo}`, to: `/knowledge?tab=graph&node=${n.id}` }));
    return [...pages, ...rs, ...ns].slice(0, 12);
  }, [q, repos.data, nodes.data]);

  if (!open) return null;
  const go = (to: string) => { onClose(); nav(to); };
  return (
    <div className="fixed inset-0 z-50 grid place-items-start bg-slate-900/40 p-4 pt-[15vh]" onMouseDown={onClose}>
      <div role="dialog" aria-label="Command palette" className="mx-auto w-full max-w-xl overflow-hidden rounded-xl bg-white shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search className="h-4 w-4 text-muted" />
          <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setIdx(0); }} aria-label="Search pages, repositories and symbols"
            placeholder="Jump to a page, repository or symbol…" className="h-11 flex-1 bg-transparent text-sm outline-none"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, items.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
              else if (e.key === "Enter" && items[idx]) go(items[idx].to);
              else if (e.key === "Escape") onClose();
            }} />
          <kbd className="rounded border border-line px-1.5 text-[10px] text-muted">esc</kbd>
        </div>
        <ul role="listbox" className="max-h-80 overflow-auto p-1">
          {items.map((it, i) => (
            <li key={it.key} role="option" aria-selected={i === idx}>
              <button onClick={() => go(it.to)} onMouseEnter={() => setIdx(i)} className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm ${i === idx ? "bg-indigo-50" : ""}`}>
                <span className="truncate font-medium">{it.label}</span><span className="ml-auto shrink-0 text-xs text-muted">{it.hint}</span>{i === idx && <CornerDownLeft className="h-3.5 w-3.5 text-muted" />}
              </button></li>))}
          {!items.length && <li className="px-3 py-6 text-center text-sm text-muted">Nothing matches “{q}”.</li>}
        </ul>
      </div>
    </div>
  );
}
