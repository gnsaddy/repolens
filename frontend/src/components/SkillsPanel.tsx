import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileUp, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react";
import { useState, type ChangeEvent } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import Modal from "@/components/Modal";
import { Badge, Button, Card, CardTitle, ErrorNote, Input, Label, Spinner, Textarea } from "@/components/ui";
import { api, type Repo, type SkillList, type SkillRow } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

const TASKS = [["lookup", "Finding a fact"], ["explain", "How and why"], ["build", "Writing something"], ["investigate", "Investigations"]] as const;
const tone = (s: SkillRow["status"]) => (s === "approved" ? "green" : s === "pending" ? "amber" : s === "rejected" ? "red" : "gray");

/** Reads a SKILL.md: optional front matter with name and description, then the instructions. */
export function parseSkillMarkdown(text: string) {
  let body = text.trim(), description = "", title = "";
  const m = body.match(/^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/);
  if (m) {
    for (const line of m[1].split("\n")) {
      const i = line.indexOf(":");
      if (i < 0) continue;
      const k = line.slice(0, i).trim().toLowerCase(), v = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
      if (k === "description") description = v;
      if ((k === "title" || k === "name") && !title) title = v;
    }
    body = m[2].trim();
  }
  if (!title) title = body.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? "";
  return { title, description, body };
}

/** Guidance for the model, written by the team: members submit, an admin approves. Private to this workspace. */
export default function SkillsPanel({ admin, canSubmit }: { admin: boolean; canSubmit: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<SkillRow | "new" | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const list = useQuery({ queryKey: ["skills"], queryFn: () => api<SkillList>("/skills") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["skills"] });
  const decide = useMutation({
    mutationFn: (v: { id: string; decision: string; note?: string }) => api(`/skills/${v.id}/review`, { method: "POST", json: { decision: v.decision, note: v.note ?? "" } }),
    onSuccess: () => { setRejecting(null); setNote(""); refresh(); },
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/skills/${id}`, { method: "DELETE" }), onSuccess: () => { refresh(); toast("Skill deleted"); } });
  const items = list.data?.items ?? [];
  const waiting = items.filter((s) => s.status === "pending");

  return (
    <div className="space-y-4">
      <Card aria-label="Skills">
        <CardTitle right={canSubmit && <Button size="sm" onClick={() => setEditing("new")}><Plus className="h-4 w-4" />Add a skill</Button>}>Skills</CardTitle>
        <p className="text-sm text-muted">A skill tells the model how your team does things: how APIs are written, what a runbook looks like, how to debug. When a question fits a skill, the best matches are added to the prompt. Skills belong to this workspace only. {admin ? "You approve what members submit." : "An admin reviews what you submit before it is used."}</p>
        <p className="mt-2 text-xs text-muted">Skills guide style and method. They cannot switch off citations, secret masking or the rule that sources are untrusted.</p>
      </Card>
      {admin && waiting.length > 0 && <p role="status" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{waiting.length} skill{waiting.length === 1 ? "" : "s"} waiting for your review.</p>}
      {list.isLoading ? <Spinner /> : !items.length ? <Card><p className="text-sm text-muted">No skills yet. {canSubmit ? "Add one, or upload a SKILL.md file." : ""}</p></Card> : (
        <ul className="space-y-3" aria-label="Skill list">{items.map((s) => (
          <li key={s.id}><Card>
            <div className="flex flex-wrap items-start gap-2">
              <div className="min-w-0 flex-1"><h3 className="font-medium">{s.title}</h3>{s.description && <p className="text-sm text-muted">{s.description}</p>}</div>
              <Badge tone={tone(s.status)}>{s.status === "pending" ? "waiting for review" : s.status}</Badge>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
              {(s.tasks.length ? s.tasks : ["any task"]).map((t) => <Badge key={t}>{t}</Badge>)}
              {s.repos.length ? s.repos.map((r) => <Badge key={r} tone="blue">{r}</Badge>) : <Badge tone="blue">all repositories</Badge>}
              {s.always && <Badge tone="brand">always</Badge>}
              <span className="ml-auto text-muted">used {s.uses} time{s.uses === 1 ? "" : "s"} · {timeAgo(s.updated_at)}</span>
            </div>
            {s.status === "rejected" && s.note && <p className="mt-2 text-sm text-red-700">Rejected: {s.note}</p>}
            <details className="mt-2"><summary className="cursor-pointer text-xs font-medium">Instructions</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">{s.body}</pre></details>
            <div className="mt-3 flex flex-wrap gap-2">
              {admin && s.status === "pending" && (<>
                <Button size="sm" onClick={() => decide.mutate({ id: s.id, decision: "approve" })}><Check className="h-3.5 w-3.5" />Approve</Button>
                <Button size="sm" variant="outline" onClick={() => setRejecting(s.id)}><X className="h-3.5 w-3.5" />Reject</Button></>)}
              {admin && s.status === "approved" && <Button size="sm" variant="outline" onClick={() => decide.mutate({ id: s.id, decision: "disable" })}>Turn off</Button>}
              {admin && (s.status === "disabled" || s.status === "rejected") && <Button size="sm" variant="outline" onClick={() => decide.mutate({ id: s.id, decision: "enable" })}>Approve</Button>}
              {(admin || (s.mine && s.status !== "approved")) && <Button size="sm" variant="ghost" onClick={() => setEditing(s)}><Pencil className="h-3.5 w-3.5" />Edit</Button>}
              {(admin || (s.mine && s.status !== "approved")) && <Button size="sm" variant="ghost" aria-label={`Delete ${s.title}`} onClick={async () => { if (await confirm({ title: `Delete "${s.title}"?`, message: "It stops applying to answers.", confirmLabel: "Delete skill", danger: true })) remove.mutate(s.id); }}><Trash2 className="h-3.5 w-3.5" /></Button>}
            </div>
            {rejecting === s.id && (
              <div className="mt-2 flex gap-2"><Input aria-label="Reason for rejecting" placeholder="Why? (shown to the author)" value={note} onChange={(e) => setNote(e.target.value)} />
                <Button size="sm" onClick={() => decide.mutate({ id: s.id, decision: "reject", note })}>Reject skill</Button></div>)}
          </Card></li>))}</ul>)}
      <ErrorNote error={decide.error ?? remove.error} />
      {!!list.data?.builtin.length && (
        <Card><CardTitle>Built in</CardTitle>
          <ul className="space-y-1 text-sm">{list.data.builtin.map((b) => <li key={b.name}><span className="font-medium">{b.title}</span> <Badge>{b.applies}</Badge><span className="block text-xs text-muted">{b.description}</span></li>)}</ul></Card>)}
      {editing && <SkillForm skill={editing === "new" ? null : editing} admin={admin} onClose={() => setEditing(null)} onSaved={(msg) => { setEditing(null); refresh(); toast(msg); }} />}
    </div>
  );
}

function SkillForm({ skill, admin, onClose, onSaved }: { skill: SkillRow | null; admin: boolean; onClose: () => void; onSaved: (msg: string) => void }) {
  const [f, setF] = useState({ title: skill?.title ?? "", description: skill?.description ?? "", body: skill?.body ?? "", tasks: skill?.tasks ?? [], repo: skill?.repo_ids[0] ?? "", always: skill?.always ?? false });
  const [err, setErr] = useState("");
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const save = useMutation({
    mutationFn: () => {
      const json = { title: f.title.trim(), description: f.description.trim(), body: f.body.trim(), tasks: f.tasks, repo_ids: f.repo ? [f.repo] : [], always: f.always };
      return skill ? api<SkillRow>(`/skills/${skill.id}`, { method: "PATCH", json }) : api<SkillRow>("/skills", { method: "POST", json });
    },
    onSuccess: (r) => onSaved(r.status === "approved" ? "Skill saved and active" : "Skill submitted for review"),
  });
  const upload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!/\.(md|markdown|txt)$/i.test(file.name)) { setErr("Upload a .md or .txt file."); return; }
    if (file.size > 100_000) { setErr("That file is too large for a skill (100 KB at most)."); return; }
    setErr("");
    const p = parseSkillMarkdown(await file.text());
    setF((x) => ({ ...x, title: p.title || x.title, description: p.description || x.description, body: p.body }));
  };
  const ok = f.title.trim().length >= 2 && f.body.trim().length >= 10;
  const toggle = (t: string) => setF((x) => ({ ...x, tasks: x.tasks.includes(t) ? x.tasks.filter((y) => y !== t) : [...x.tasks, t] }));
  return (
    <Modal wide title={skill ? "Edit skill" : "Add a skill"} icon={<Sparkles className="mt-1 h-5 w-5 text-brand" />} onClose={onClose}
      subtitle={admin ? "It starts working as soon as you save it." : "An admin reviews it before it is used."}>
      <form className="space-y-4 text-sm" onSubmit={(e) => { e.preventDefault(); if (ok) save.mutate(); }}>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-xs hover:bg-slate-50"><FileUp className="h-4 w-4" />Upload a SKILL.md<input type="file" accept=".md,.markdown,.txt" className="sr-only" aria-label="Upload a skill file" onChange={upload} /></label>
        {err && <p role="alert" className="text-xs text-red-700">{err}</p>}
        <label className="block"><Label>Name</Label><Input aria-label="Name" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="How we write API endpoints" /></label>
        <label className="block"><Label>When should it apply?</Label><Input aria-label="When should it apply?" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} placeholder="Use when asked to write, add or change an API endpoint" /><span className="text-xs text-muted">The model sees this skill when a question shares words with the name and this line.</span></label>
        <label className="block"><Label>Instructions</Label><Textarea aria-label="Instructions" required rows={8} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} placeholder="Every endpoint calls authorize() first, returns a response model, and has a regression test." /><span className="text-xs text-muted">{f.body.length.toLocaleString()} / 8,000 characters. Secrets are removed when you save.</span></label>
        <fieldset><Label>Applies to</Label><div className="flex flex-wrap gap-3">{TASKS.map(([k, l]) => <label key={k} className="flex items-center gap-1.5"><input type="checkbox" checked={f.tasks.includes(k)} onChange={() => toggle(k)} />{l}</label>)}</div><span className="text-xs text-muted">None ticked means every kind of question.</span></fieldset>
        <label className="block"><Label>Repository</Label><select aria-label="Repository" value={f.repo} onChange={(e) => setF({ ...f, repo: e.target.value })} className="h-9 rounded-md border border-line bg-white px-2"><option value="">All repositories</option>{(repos.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
        <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={f.always} onChange={(e) => setF({ ...f, always: e.target.checked })} /><span>Apply to every matching kind of question, not only when the words match<span className="block text-xs text-muted">For house rules such as "always cite the file path".</span></span></label>
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button disabled={!ok || save.isPending}>{admin ? "Save" : "Submit for review"}</Button></div>
      </form>
    </Modal>
  );
}
