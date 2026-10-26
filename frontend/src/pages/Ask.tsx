import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Bot, Check, ClipboardCheck, Copy, FileText, GitBranch, MessageSquarePlus, MoreVertical, Paperclip, Pencil, Send, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, ErrorNote, Input, Spinner, Textarea } from "@/components/ui";
import { api, type ChatList, type ChatMsg, type ChatSource, type ChatSummary, type ChatThread, type Repo } from "@/lib/api";
import { cn, safeHref, timeAgo } from "@/lib/utils";
import ChatGaps from "@/components/ChatGaps";
import InfoTip from "@/components/InfoTip";
import Markdown from "./wiki/Markdown";

const MAX_ATTACH_CHARS = 400_000;
const EXAMPLES = ["How does the sign-in flow work?", "Where is the retry policy documented?", "What depends on the payment service?"];
type Attached = { name: string; text: string; cut: boolean };

const group = (iso: string) => {
  const days = (Date.now() - new Date(iso).getTime()) / 86_400_000;
  return days < 1 ? "Today" : days < 2 ? "Yesterday" : days < 8 ? "Previous 7 days" : "Older";
};

/** A conversation with the knowledge base: history on the left, the chat on the right. */
export default function Ask() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const toast = useToast();
  const [tab, setTab] = useState<"chats" | "archived" | "gaps">("chats");
  const [search, setSearch] = useState("");
  const [draftScope, setDraftScope] = useState("");
  const [draftFallback, setDraftFallback] = useState(false);
  const [menu, setMenu] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [title, setTitle] = useState("");

  const list = useQuery({ queryKey: ["chats", tab, search], queryFn: () => api<ChatList>(`/chats?archived=${tab === "archived"}&q=${encodeURIComponent(search)}`), enabled: tab !== "gaps" });
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const thread = useQuery({ queryKey: ["chat", id], enabled: !!id, queryFn: () => api<ChatThread>(`/chats/${id}`) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["chats"] }); if (id) qc.invalidateQueries({ queryKey: ["chat", id] }); };

  useEffect(() => { if (!menu) return; const close = () => setMenu(null); window.addEventListener("click", close); return () => window.removeEventListener("click", close); }, [menu]);

  const patch = useMutation({
    mutationFn: (v: { id: string; body: Record<string, unknown> }) => api<ChatThread>(`/chats/${v.id}`, { method: "PATCH", json: v.body }),
    onSuccess: (t) => { qc.setQueryData(["chat", t.id], t); qc.invalidateQueries({ queryKey: ["chats"] }); },
  });
  const remove = useMutation({
    mutationFn: (cid: string) => api(`/chats/${cid}`, { method: "DELETE" }),
    onSuccess: (_r, cid) => { qc.invalidateQueries({ queryKey: ["chats"] }); qc.removeQueries({ queryKey: ["chat", cid] }); if (cid === id) nav("/ask"); toast("Chat deleted"); },
  });

  const removeChat = async (c: { id: string; title: string }) => {
    if (await confirm({ title: `Delete "${c.title}"?`, message: "The whole conversation is deleted and cannot be brought back.", confirmLabel: "Delete chat", danger: true })) remove.mutate(c.id);
  };
  const startRename = (c: ChatSummary) => { setRenaming(c.id); setTitle(c.title); setMenu(null); };
  const saveRename = () => { if (renaming && title.trim()) patch.mutate({ id: renaming, body: { title: title.trim() } }); setRenaming(null); };

  const groups = useMemo(() => {
    const out = new Map<string, ChatSummary[]>();
    for (const c of list.data?.items ?? []) out.set(group(c.updated_at), [...(out.get(group(c.updated_at)) ?? []), c]);
    return [...out.entries()];
  }, [list.data]);
  const modelAvailable = list.data?.model_available ?? false;
  const current = thread.data;

  return (
    <div className="grid gap-4 lg:h-[calc(100vh-6.5rem)] lg:grid-cols-[320px_minmax(0,1fr)]">
      <Card className="flex min-h-0 flex-col gap-3 p-3" aria-label="Chat history">
        <Button onClick={() => nav("/ask")} className="w-full"><MessageSquarePlus className="h-4 w-4" />New chat</Button>
        <Input aria-label="Search chats" placeholder="Search chats…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div role="tablist" aria-label="Chat list" className="flex gap-1 text-sm">
          {(["chats", "archived", "gaps"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={cn("rounded-md px-3 py-1", tab === t ? "bg-indigo-50 font-medium text-brand" : "text-muted hover:bg-slate-50")}>{t === "chats" ? "Chats" : t === "archived" ? "Archived" : "Gaps"}</button>))}
        </div>
        <div className="min-h-0 flex-1 overflow-auto" aria-label={tab === "chats" ? "Chats" : tab === "archived" ? "Archived chats" : "Gaps"}>
          {tab === "gaps" ? <ChatGaps /> : list.isLoading ? <Spinner /> : !groups.length ? <p className="p-2 text-sm text-muted">{search ? "No chats match that search." : tab === "archived" ? "No archived chats." : "No chats yet. Ask a question to start one."}</p> : groups.map(([name, items]) => (
            <section key={name} className="mb-3"><h3 className="mb-1 px-2 text-xs font-medium uppercase tracking-wide text-muted">{name}</h3>
              <ul className="space-y-0.5">{items.map((c) => (
                <li key={c.id} className={cn("group relative rounded-md", c.id === id ? "bg-indigo-50" : "hover:bg-slate-50")}>
                  {renaming === c.id ? (
                    <div className="flex items-center gap-1 p-1.5"><Input aria-label="Chat title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") setRenaming(null); }} />
                      <button aria-label="Save title" className="p-1 text-emerald-600" onClick={saveRename}><Check className="h-4 w-4" /></button></div>
                  ) : (
                    <div className="flex items-center">
                      <Link to={`/ask/${c.id}`} className="min-w-0 flex-1 px-2 py-1.5 text-sm" aria-current={c.id === id ? "page" : undefined}>
                        <span className={cn("block truncate", c.id === id && "font-medium text-brand")}>{c.title}</span>
                        {c.preview && <span className="block truncate text-xs text-muted">{c.preview}</span>}
                      </Link>
                      <button aria-label={`Options for ${c.title}`} aria-haspopup="menu" className="mr-1 rounded p-1 text-muted opacity-60 hover:bg-white hover:opacity-100 group-hover:opacity-100" onClick={(e) => { e.stopPropagation(); setMenu(menu === c.id ? null : c.id); }}><MoreVertical className="h-4 w-4" /></button>
                    </div>)}
                  {menu === c.id && (
                    <div role="menu" aria-label={`Actions for ${c.title}`} className="absolute right-1 top-9 z-20 w-40 rounded-lg border border-line bg-white p-1 text-sm shadow-lg" onClick={(e) => e.stopPropagation()}>
                      <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-slate-50" onClick={() => startRename(c)}><Pencil className="h-3.5 w-3.5" />Rename</button>
                      <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-slate-50" onClick={() => { setMenu(null); patch.mutate({ id: c.id, body: { archived: !c.archived } }); toast(c.archived ? "Chat restored" : "Chat archived"); }}>{c.archived ? <ArchiveRestore className="h-3.5 w-3.5" /> : <Archive className="h-3.5 w-3.5" />}{c.archived ? "Restore" : "Archive"}</button>
                      <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-red-600 hover:bg-red-50" onClick={() => { setMenu(null); void removeChat(c); }}><Trash2 className="h-3.5 w-3.5" />Delete</button>
                    </div>)}
                </li>))}</ul>
            </section>))}
        </div>
      </Card>

      <Chat key={id ?? "new"} thread={id ? current : undefined} loading={!!id && thread.isLoading} error={thread.error} repos={repos.data ?? []} modelAvailable={modelAvailable}
        draft={{ scope: draftScope, fallback: draftFallback, setScope: setDraftScope, setFallback: setDraftFallback }}
        onSaved={(t) => { qc.setQueryData(["chat", t.id], t); refresh(); if (t.id !== id) nav(`/ask/${t.id}`); }}
        onChange={(body) => id && patch.mutate({ id, body })} onDelete={() => current && void removeChat(current)} onRename={() => current && startRename(current)} />
    </div>
  );
}

interface ChatProps {
  thread?: ChatThread; loading: boolean; error: unknown; repos: Repo[]; modelAvailable: boolean;
  draft: { scope: string; fallback: boolean; setScope: (s: string) => void; setFallback: (b: boolean) => void };
  onSaved: (t: ChatThread) => void; onChange: (body: Record<string, unknown>) => void; onDelete: () => void; onRename: () => void;
}

function Chat({ thread, loading, error, repos, modelAvailable, draft, onSaved, onChange, onDelete, onRename }: ChatProps) {
  const [text, setText] = useState("");
  const [attached, setAttached] = useState<Attached | null>(null);
  const [fileError, setFileError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const toast = useToast();
  const end = useRef<HTMLDivElement>(null);
  const scope = thread ? (thread.repo_ids[0] ?? "") : draft.scope;
  const fallback = thread ? thread.fallback : draft.fallback;
  const items = thread?.items ?? [];

  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [items.length, pending]);

  const send = useMutation({
    mutationFn: async (v: { content: string; attachment: Attached | null }) => {
      let t = thread;
      if (!t) t = await api<ChatThread>("/chats", { method: "POST", json: { repo_ids: draft.scope ? [draft.scope] : [], fallback: draft.fallback } });
      const r = await api<ChatThread>(`/chats/${t.id}/messages`, { method: "POST", json: { content: v.content, ...(v.attachment ? { attachment: { name: v.attachment.name, text: v.attachment.text } } : {}) } });
      return { ...t, ...r, items: [...t.items, ...r.items] } as ChatThread;
    },
    onMutate: (v) => { setPending(v.content); setText(""); setAttached(null); },  // the box clears at once; the question shows above while it is answered
    onError: (_e, v) => { setText(v.content); setAttached(v.attachment); },  // a failed send gives the question back
    onSuccess: (t) => onSaved(t),
    onSettled: () => setPending(null),
  });
  const edit = useMutation({
    mutationFn: (v: { mid: string; content: string }) => api<ChatThread>(`/chats/${thread!.id}/messages/${v.mid}`, { method: "PUT", json: { content: v.content } }),
    onSuccess: (r) => { setEditing(null); onSaved({ ...thread!, ...r }); },
  });

  const submit = () => { const c = text.trim(); if (c && !send.isPending) send.mutate({ content: c, attachment: attached }); };
  const pick = async (f: File | undefined) => {
    setFileError("");
    if (!f) return;
    if (!/\.(txt|log)$/i.test(f.name)) { setFileError("Only .txt and .log files can be attached."); return; }
    const raw = await f.text();
    setAttached({ name: f.name, text: raw.slice(0, MAX_ATTACH_CHARS), cut: raw.length > MAX_ATTACH_CHARS });
  };
  const saveTest = async (i: number) => {
    const q = items[i - 1];
    const files = [...new Set(items[i].sources.filter((s) => new RegExp(`\\[${s.id}\\]`).test(items[i].content)).map((s) => s.file))].slice(0, 5);
    try { await api("/golden/questions", { method: "POST", json: { question: q.content.slice(0, 1000), expect_files: files } }); toast(files.length ? "Saved as a quality check: this question should keep finding these files" : "Saved as a quality check"); } catch (e) { toast((e as Error).message, "error"); }
  };
  const copy = async (m: ChatMsg) => { try { await navigator.clipboard.writeText(m.content); setCopied(m.id); setTimeout(() => setCopied(null), 1500); } catch { /* clipboard unavailable */ } };

  return (
    <Card className="flex min-h-0 flex-col p-0" aria-label="Conversation panel">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
        <label className="flex items-center gap-2 text-sm"><GitBranch className="h-4 w-4 text-muted" /><span className="text-muted">Scope:</span>
          <select aria-label="Scope" value={scope} onChange={(e) => (thread ? onChange({ repo_ids: e.target.value ? [e.target.value] : [] }) : draft.setScope(e.target.value))} className="h-9 max-w-[16rem] rounded-md border border-line bg-white px-2 text-sm font-medium">
            <option value="">All repositories &amp; knowledge base</option>{repos.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
        <span className="ml-auto flex items-center gap-1">
          <label className={cn("flex items-center gap-2 text-sm", !modelAvailable && "opacity-60")} title={modelAvailable ? undefined : "Needs a connected language model (Settings → AI & search)"}>
            <input type="checkbox" checked={fallback} disabled={!modelAvailable} onChange={(e) => (thread ? onChange({ fallback: e.target.checked }) : draft.setFallback(e.target.checked))} />Allow fallback knowledge</label>
          <InfoTip label="About fallback knowledge">
            <strong className="block text-sm">Fallback knowledge</strong>
            <span className="mt-1 block">Normally answers come only from your repositories and documents, with citations.</span>
            <span className="mt-1 block">With this on, a connected model may answer from its general knowledge when your sources do not cover the question. Those parts start with “Not from your documents:”, have no citations, and can be wrong or out of date.</span>
            <span className="mt-1 block">Your question and any attached file are sent to your model provider even when nothing matched. Off by default. Needs a connected model.</span>
          </InfoTip>
        </span>
        {thread && (<>
          <button aria-label="Rename chat" title="Rename chat" className="rounded p-1.5 text-muted hover:bg-slate-100" onClick={onRename}><Pencil className="h-4 w-4" /></button>
          <button aria-label={thread.archived ? "Restore chat" : "Archive chat"} title={thread.archived ? "Restore" : "Archive"} className="rounded p-1.5 text-muted hover:bg-slate-100" onClick={() => onChange({ archived: !thread.archived })}>{thread.archived ? <ArchiveRestore className="h-4 w-4" /> : <Archive className="h-4 w-4" />}</button>
          <button aria-label="Delete chat" title="Delete chat" className="rounded p-1.5 text-muted hover:bg-red-50 hover:text-red-600" onClick={onDelete}><Trash2 className="h-4 w-4" /></button>
        </>)}
      </div>

      <div role="log" aria-label="Conversation" className="min-h-0 flex-1 space-y-5 overflow-auto px-4 py-5">
        {thread?.archived && <p className="rounded-lg bg-amber-50 p-2.5 text-xs text-amber-900">This chat is archived. Writing in it brings it back.</p>}
        {loading ? <Spinner /> : error ? <ErrorNote error={error} /> : !items.length && !pending && (
          <div className="flex gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-slate-100"><Bot className="h-5 w-5 text-brand" /></div>
            <div className="rounded-xl border border-line p-4 text-sm leading-6">Ask anything about your connected repositories and documents: how a flow works, where something lives, or what depends on what. Answers cite the sources they used. Attach a <b>.txt</b> or <b>.log</b> file to debug an error.
              <div className="mt-3 flex flex-wrap gap-2">{EXAMPLES.map((x) => <button key={x} className="rounded-full border border-line px-3 py-1 text-xs hover:bg-slate-50" onClick={() => setText(x)}>{x}</button>)}</div></div></div>)}
        {items.map((m) => m.role === "user" ? (
          <div key={m.id} className="group flex justify-end gap-2">
            {editing === m.id ? (
              <div className="w-full max-w-2xl space-y-2"><Textarea aria-label="Edit your message" rows={3} value={editText} onChange={(e) => setEditText(e.target.value)} />
                <div className="flex justify-end gap-2"><Button size="sm" variant="outline" onClick={() => setEditing(null)}>Cancel</Button><Button size="sm" disabled={!editText.trim() || edit.isPending} onClick={() => edit.mutate({ mid: m.id, content: editText.trim() })}>Save and answer again</Button></div>
                <ErrorNote error={edit.error} /></div>
            ) : (<>
              <button aria-label="Edit message" title="Edit and ask again" className="self-center rounded p-1.5 text-muted opacity-0 hover:bg-slate-100 focus:opacity-100 group-hover:opacity-100" onClick={() => { setEditing(m.id); setEditText(m.content); }}><Pencil className="h-3.5 w-3.5" /></button>
              <div className="max-w-2xl rounded-2xl bg-brand px-4 py-2.5 text-sm text-white">
                <p className="whitespace-pre-wrap">{m.content}</p>
                {m.attachment && <p className="mt-1.5 inline-flex items-center gap-1 rounded bg-white/15 px-2 py-0.5 text-xs"><FileText className="h-3 w-3" />{m.attachment.name}</p>}
                {m.edited_at && <p className="mt-1 text-right text-[10px] opacity-70">edited</p>}</div></>)}
          </div>
        ) : (
          <div key={m.id} className="flex gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-slate-100"><Bot className="h-5 w-5 text-brand" /></div>
            <div className="min-w-0 max-w-3xl flex-1 rounded-xl border border-line p-4">
              <div className="mb-2 flex items-center gap-2"><Badge tone={m.mode === "llm" ? "brand" : m.mode === "general" ? "amber" : m.mode === "failed" ? "red" : "gray"}>{m.mode === "llm" ? "AI answer" : m.mode === "general" ? "general knowledge" : m.mode === "failed" ? "AI unavailable" : m.mode === "extractive" ? "extracted passages" : "no results"}</Badge>
                {m.mode === "llm" && /\(Check these names, not found/.test(m.content) && <Badge tone="amber" title="The answer names files, routes or functions that were not found in your indexed repositories">Check these names</Badge>}
                {m.mode === "llm" && /^\s*Not from your documents:/m.test(m.content) && <Badge tone="amber" title="Part of this answer is not backed by your sources and has no citations">Not from your documents</Badge>}
                <span className="text-xs text-muted">{timeAgo(m.created_at)}</span>
                {items[items.indexOf(m) - 1]?.role === "user" && m.mode !== "failed" && <button aria-label="Save as a test" title="Save as a quality check" className="ml-auto rounded p-1 text-muted hover:bg-slate-100" onClick={() => void saveTest(items.indexOf(m))}><ClipboardCheck className="h-4 w-4" /></button>}
                <button aria-label="Copy answer" title="Copy" className={cn("rounded p-1 text-muted hover:bg-slate-100", items[items.indexOf(m) - 1]?.role !== "user" && "ml-auto")} onClick={() => void copy(m)}>{copied === m.id ? <Check className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4" />}</button></div>
              <div className="text-sm leading-6"><Markdown text={m.content} onOpen={() => {}} /></div>
              {m.mode === "failed" && (() => { const i = items.indexOf(m); const q = items[i - 1]; return q?.role === "user" && i === items.length - 1 ? <Button size="sm" variant="outline" className="mt-3" disabled={edit.isPending} onClick={() => edit.mutate({ mid: q.id, content: q.content })}>{edit.isPending ? "Trying…" : "Try again"}</Button> : null; })()}
              <Sources sources={m.sources} />
            </div>
          </div>))}
        {pending && (<>
          {!items.some((m) => m.content === pending && m.role === "user") && <div className="flex justify-end"><div className="max-w-2xl rounded-2xl bg-brand px-4 py-2.5 text-sm text-white"><p className="whitespace-pre-wrap">{pending}</p></div></div>}
          <div className="flex gap-3" role="status" aria-label="Thinking"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-slate-100"><Bot className="h-5 w-5 text-brand" /></div><div className="flex items-center gap-2 rounded-xl border border-line px-4 py-3 text-sm text-muted"><Spinner />Searching your knowledge base…</div></div></>)}
        <div ref={end} />
      </div>

      <div className="border-t border-line p-3">
        <ErrorNote error={send.error} />
        {(fileError || attached?.cut) && <p role="alert" className="mb-2 text-xs text-amber-700">{fileError || `${attached!.name} is very large, so only the first ${MAX_ATTACH_CHARS.toLocaleString()} characters are sent.`}</p>}
        {attached && <div className="mb-2 inline-flex items-center gap-2 rounded-lg border border-line bg-slate-50 px-2.5 py-1 text-xs"><FileText className="h-3.5 w-3.5 text-brand" /><span className="font-medium">{attached.name}</span><span className="text-muted">{Math.max(1, Math.round(attached.text.length / 1024))} KB</span>
          <button aria-label="Remove attachment" className="text-muted hover:text-red-600" onClick={() => setAttached(null)}><X className="h-3.5 w-3.5" /></button></div>}
        <div className="flex items-end gap-2">
          <label className="grid h-11 w-11 shrink-0 cursor-pointer place-items-center rounded-lg border border-line text-muted hover:bg-slate-50" title="Attach a .txt or .log file to debug">
            <Paperclip className="h-5 w-5" /><span className="sr-only">Attach a .txt or .log file</span>
            <input type="file" accept=".txt,.log,text/plain" aria-label="Attach file" className="sr-only" onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = ""; }} /></label>
          <Textarea aria-label="Message" rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask anything across repositories, architectures, API routes, or code flow…" className="max-h-40 min-h-11 flex-1 resize-none"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
          <Button className="h-11 shrink-0 px-5" disabled={!text.trim() || send.isPending} onClick={submit}><Send className="h-4 w-4" />Send</Button>
        </div>
        <p className="mt-1.5 text-[11px] text-muted">Enter sends, Shift+Enter adds a line. Chats are private to you.</p>
      </div>
    </Card>
  );
}

function Sources({ sources }: { sources: ChatSource[] }) {
  if (!sources.length) return null;
  const href = (s: ChatSource) => (s.kind === "doc" ? `/wiki?tab=browse&repo=${encodeURIComponent(s.repo_id)}&path=${encodeURIComponent(s.file)}` : s.node_id ? `/knowledge?tab=graph&node=${encodeURIComponent(s.node_id)}` : null);
  return (
    <details className="mt-3 border-t border-line pt-2 text-xs">
      <summary className="cursor-pointer font-medium text-muted">Sources ({sources.length})</summary>
      <ul className="mt-2 space-y-1.5">{sources.map((s) => {
        const to = href(s), ext = safeHref(s.url);
        const label = <><b>[{s.id}]</b> {s.repo}:{s.file}{s.start_line ? `:${s.start_line}` : ""}</>;
        return (<li key={s.id}>{to ? <Link className="text-brand hover:underline" to={to}>{label}</Link> : ext ? <a className="text-brand hover:underline" href={ext} target="_blank" rel="noopener noreferrer">{label}</a> : <span>{label}</span>}
          <span className="block truncate text-muted">{s.snippet.replace(/\s+/g, " ").slice(0, 140)}</span></li>);
      })}</ul>
    </details>
  );
}
