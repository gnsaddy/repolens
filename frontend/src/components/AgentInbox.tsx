import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowSquareOut, Tray } from "@phosphor-icons/react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Spinner } from "@/components/ui";
import { api, type AgentDraft } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

type Tab = "open" | "posted" | "archived";
const TABS: [Tab, string][] = [["open", "Waiting"], ["posted", "Posted"], ["archived", "Archived"]];
const EMPTY: Record<Tab, string> = { open: "Nothing is waiting for you.", posted: "Nothing has been posted on a pull request yet.", archived: "Nothing is archived." };
const KIND = { pr_comment: "comment for the pull request", investigation: "CI failure", wiki_proposal: "drafted page update", watch: "nightly watch" } as const;

/** The comment as it was put on GitHub, with where it went and who approved it. */
function PostedComment({ d }: { d: AgentDraft }) {
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 caption-5 text-muted">
        <span>Posted {d.decided_at ? timeAgo(d.decided_at) : ""}{d.decided_by ? ` by ${d.decided_by}` : ""}</span>
        <a className="inline-flex items-center gap-1 text-brand hover:underline" href={d.posted_url} target="_blank" rel="noopener noreferrer">View on GitHub<ArrowSquareOut className="h-3 w-3" aria-hidden /></a>
      </p>
      <details>
        <summary className="cursor-pointer caption-5 text-muted hover:text-ink">View the posted comment</summary>
        <pre aria-label={`Posted comment for ${d.title}`} className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono caption-5 text-ink">{d.body}</pre>
      </details>
    </div>
  );
}

function Draft({ d, tab, done }: { d: AgentDraft; tab: Tab; done: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [text, setText] = useState(d.body);
  const post = useMutation({
    mutationFn: () => api<{ posted_url: string }>(`/automations/inbox/${d.id}/approve`, { method: "POST", json: { body: text } }),
    onSuccess: () => { done(); toast("Comment posted on the pull request"); },
  });
  const mark = useMutation({
    mutationFn: (status: "read" | "archived" | "restore") => api(`/automations/inbox/${d.id}`, { method: "PATCH", json: { status } }),
    onSuccess: (_x, status) => { done(); if (status === "archived") toast("Archived"); if (status === "restore") toast("Restored"); },
  });
  const comment = d.kind === "pr_comment";
  const posted = !!d.posted_url;
  return (
    <li className="space-y-3 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="title-4">{d.title || "Pull request review"}</span>
        <span className="caption-5 text-muted">{KIND[d.kind]} · {d.repo} · {timeAgo(d.created_at)}</span>
        {d.status === "new" && <Badge tone="blue">new</Badge>}
        {tab === "archived" && posted && <Badge tone="gray" title="It stays on the pull request on GitHub">was posted</Badge>}
        {d.link && <a className="inline-flex items-center gap-1 caption-5 text-brand hover:underline" href={d.link} target="_blank" rel="noopener noreferrer">{comment ? "Pull request" : "CI run"}<ArrowSquareOut className="h-3 w-3" aria-hidden /></a>}
      </div>
      {comment && posted ? (
        <PostedComment d={d} />
      ) : comment && d.pending ? (
        <p className="caption-5 text-muted">Waiting for the investigation to finish. The comment is written when it has a result, and then it can be posted.</p>
      ) : comment && tab === "open" ? (
        <details className="group">
          <summary className="cursor-pointer caption-5 text-muted hover:text-ink">Read and edit the comment before it is posted</summary>
          <textarea aria-label={`Comment for ${d.title}`} className="mt-2 h-64 w-full rounded-lg border border-line bg-white p-3 font-mono caption-5 text-ink" value={text} onChange={(e) => setText(e.target.value)} />
          <p className="caption-5 text-muted">A line is added at the end saying an AI wrote it and a person approved it.</p>
        </details>
      ) : comment ? (
        <details>
          <summary className="cursor-pointer caption-5 text-muted hover:text-ink">View the draft</summary>
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono caption-5 text-ink">{d.body}</pre>
        </details>
      ) : d.kind === "wiki_proposal" ? (
        <p className="caption-5 text-muted">{d.body} <Link className="text-brand hover:underline" to="/wiki">Open the wiki</Link></p>
      ) : d.kind === "watch" ? (
        <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 caption-5 text-muted">{d.body}</pre>
      ) : (
        <ul className="space-y-1">
          {d.investigations.map((i, n) => (
            <li key={i.id} className="flex flex-wrap items-center gap-2 caption-5"><Link className="text-brand hover:underline" to={`/investigations/${i.id}`}>Investigation {n + 1}</Link><span className="text-muted">{i.status}</span>
              {i.ai ? <Badge tone="green" title="The text of this report was written by an AI model">AI written{i.models.length ? ` · ${i.models.join(", ")}` : ""}</Badge> : <Badge tone="gray" title="Built from evidence by code">evidence only</Badge>}</li>))}
        </ul>
      )}
      {d.error && tab !== "archived" && <ErrorNote error={new Error(d.error)} />}
      {post.error ? <ErrorNote error={post.error} /> : null}
      <div className="flex flex-wrap gap-2">
        {tab === "open" && comment && <Button size="sm" disabled={post.isPending || !text.trim() || !!d.pending} onClick={async () => { if (await confirm({ title: "Post this comment on the pull request?", message: "Everyone who can see the pull request will see it. It is posted with your GitHub connection.", confirmLabel: "Post comment" })) post.mutate(); }}>Post to the pull request</Button>}
        {tab === "open" && d.status === "new" && <Button size="sm" variant="outline" disabled={mark.isPending} onClick={() => mark.mutate("read")}>Mark read</Button>}
        {tab !== "archived" && <Button size="sm" variant="outline" disabled={mark.isPending} onClick={() => mark.mutate("archived")}>Archive</Button>}
        {tab === "archived" && <Button size="sm" variant="outline" disabled={mark.isPending} onClick={() => mark.mutate("restore")}>Restore</Button>}
      </div>
      {mark.error ? <ErrorNote error={mark.error} /> : null}
    </li>
  );
}

/** What the agent made. Waiting drafts, what was posted on a pull request, and what was set aside. A comment for a pull
 *  request is posted only when an admin approves it. */
export default function AgentInbox() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("open");
  const q = useQuery({ queryKey: ["agent-inbox", tab], queryFn: () => api<{ drafts: AgentDraft[]; counts: Record<Tab, number> }>(`/automations/inbox?status=${tab}`), refetchInterval: 15000 });
  const done = () => { qc.invalidateQueries({ queryKey: ["agent-inbox"] }); qc.invalidateQueries({ queryKey: ["automations"] }); qc.invalidateQueries({ queryKey: ["agent-summary"] }); };
  const counts = q.data?.counts;
  return (
    <Card aria-label="Inbox">
      <CardTitle icon={Tray} right={<span className="caption-5 text-muted">{counts?.open ?? 0} waiting</span>}>Inbox</CardTitle>
      <p className="mb-3 caption-5 text-muted">Nothing the agent writes goes to GitHub by itself. A comment waits here until you read it, edit it if you like, and post it. What you posted stays under Posted; Archive sets a draft or a posted comment aside (a posted comment stays on GitHub).</p>
      <div role="tablist" aria-label="Inbox sections" className="mb-2 flex gap-1 border-b border-line">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={cn("-mb-px border-b-2 px-3 py-2 body-6", tab === k ? "border-brand text-brand" : "border-transparent text-muted hover:text-ink")}>{label}{counts ? ` ${counts[k]}` : ""}</button>))}
      </div>
      {q.isLoading ? <Spinner /> : !q.data ? <ErrorNote error={q.error} /> : !q.data.drafts.length ? <Empty>{EMPTY[tab]}</Empty> : <ul className="divide-y divide-line">{q.data.drafts.map((d) => <Draft key={d.id} d={d} tab={tab} done={done} />)}</ul>}
    </Card>
  );
}
