import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/components/feedback";
import { Button, ErrorNote, Input } from "@/components/ui";
import { api, type MemoryEntry, type SkillFile } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

/** Look through remembered answers (deep search reads the answers too) and package a selection as a Claude skill. */
export default function MemoryBrowser({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [deep, setDeep] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [skill, setSkill] = useState<SkillFile | null>(null);
  const list = useQuery({ queryKey: ["memory-entries", q, deep], queryFn: () => api<MemoryEntry[]>(`/settings/memory/entries?q=${encodeURIComponent(q)}&deep=${deep}`) });
  const forget = useMutation({ mutationFn: (id: string) => api(`/settings/memory/entries/${id}`, { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["memory-entries"] }); qc.invalidateQueries({ queryKey: ["answer-memory"] }); toast("Forgotten"); } });
  const make = useMutation({ mutationFn: () => api<SkillFile>("/settings/memory/skill", { method: "POST", json: { entry_ids: [...picked] } }), onSuccess: setSkill });
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const download = (f: SkillFile) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([f.markdown], { type: "text/markdown" })); a.download = "SKILL.md"; a.click(); URL.revokeObjectURL(a.href); };
  if (skill) return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Save this as <code>{skill.filename}</code> in a <code>.claude/skills</code> folder (or upload it as a skill in Claude). It contains {skill.entries} answer{skill.entries === 1 ? "" : "s"}; secrets were removed.</p>
      <pre aria-label="Skill file" className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-xs">{skill.markdown}</pre>
      <div className="flex gap-2"><Button size="sm" onClick={() => download(skill)}><Download className="h-3.5 w-3.5" />Download SKILL.md</Button>
        <Button size="sm" variant="outline" onClick={() => { void navigator.clipboard?.writeText(skill.markdown); toast("Copied"); }}><Copy className="h-3.5 w-3.5" />Copy</Button>
        <Button size="sm" variant="ghost" onClick={() => setSkill(null)}>Back</Button></div>
    </div>);
  return (
    <div className="space-y-3">
      <form className="flex flex-wrap items-center gap-2" role="search" onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }}>
        <Input aria-label="Search memory" className="min-w-56 flex-1" placeholder="Search questions and answers…" value={text} onChange={(e) => { setText(e.target.value); if (!e.target.value) setQ(""); }} />
        <label className="flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} />Deep: also read answers</label>
        <Button variant="outline" size="sm">Search</Button>
      </form>
      {list.isLoading ? null : !list.data?.length ? <p className="py-6 text-center text-sm text-muted">{q ? `Nothing in memory matches “${q}”.` : "Nothing is remembered yet."}</p> : (
        <ul aria-label="Remembered answers" className="space-y-2">{list.data.map((e) => (
          <li key={e.id} className="rounded-lg border border-line p-3 text-sm">
            <div className="flex items-start gap-2">
              <input type="checkbox" aria-label={`Select ${e.asked}`} className="mt-1" checked={picked.has(e.id)} onChange={() => toggle(e.id)} />
              <div className="min-w-0 flex-1">
                <div className="font-medium">{e.asked}</div>
                <div className="text-xs text-muted">{timeAgo(e.created_at)} · reused {e.hits}× · {e.tokens.toLocaleString()} tokens to produce{e.score != null ? ` · ${Math.round(e.score * 100)}% match in the ${e.matched}` : ""}</div>
                <details className="mt-1"><summary className="cursor-pointer text-xs text-brand">Show answer</summary><p className="mt-1 whitespace-pre-wrap leading-6">{e.answer}</p>{!!e.sources.length && <p className="mt-1 text-xs text-muted">Sources: {e.sources.join(", ")}</p>}</details>
              </div>
              {admin && <button aria-label={`Forget ${e.asked}`} className="text-muted hover:text-red-600" onClick={() => forget.mutate(e.id)}><Trash2 className="h-4 w-4" /></button>}
            </div>
          </li>))}</ul>)}
      <ErrorNote error={forget.error ?? make.error} />
      <div className="sticky bottom-0 flex items-center justify-between border-t border-line bg-white pt-3">
        <span className="text-xs text-muted">{picked.size} selected</span>
        <Button disabled={!picked.size || make.isPending} onClick={() => make.mutate()}><Sparkles className="h-4 w-4" />{make.isPending ? "Building…" : "Create skill from selected"}</Button>
      </div>
    </div>
  );
}
