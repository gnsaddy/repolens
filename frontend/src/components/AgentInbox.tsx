import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowSquareOut, Tray } from "@phosphor-icons/react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Spinner } from "@/components/ui";
import { api, type AgentDraft } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

function Draft({ d, done }: { d: AgentDraft; done: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [text, setText] = useState(d.body);
  const post = useMutation({
    mutationFn: () => api<{ posted_url: string }>(`/automations/inbox/${d.id}/approve`, { method: "POST", json: { body: text } }),
    onSuccess: () => { done(); toast("Comment posted on the pull request"); },
  });
  const mark = useMutation({
    mutationFn: (status: "read" | "dismissed") => api(`/automations/inbox/${d.id}`, { method: "PATCH", json: { status } }),
    onSuccess: done,
  });
  const comment = d.kind === "pr_comment";
  const KIND = { pr_comment: "comment for the pull request", investigation: "CI failure", wiki_proposal: "drafted page update", watch: "nightly watch" } as const;
  return (
    <li className="space-y-3 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="title-4">{d.title || "Pull request review"}</span>
        <span className="caption-5 text-muted">{KIND[d.kind]} · {d.repo} · {timeAgo(d.created_at)}</span>
        {d.status === "new" && <Badge tone="blue">new</Badge>}
        {d.link && <a className="inline-flex items-center gap-1 caption-5 text-brand hover:underline" href={d.link} target="_blank" rel="noopener noreferrer">{comment ? "Pull request" : "CI run"}<ArrowSquareOut className="h-3 w-3" aria-hidden /></a>}
      </div>
      {comment && d.pending ? (
        <p className="caption-5 text-muted">Waiting for the investigation to finish. The comment is written when it has a result, and then it can be posted.</p>
      ) : comment ? (
        <details className="group">
          <summary className="cursor-pointer caption-5 text-muted hover:text-ink">Read and edit the comment before it is posted</summary>
          <textarea aria-label={`Comment for ${d.title}`} className="mt-2 h-64 w-full rounded-lg border border-line bg-white p-3 font-mono caption-5 text-ink" value={text} onChange={(e) => setText(e.target.value)} />
          <p className="caption-5 text-muted">A line is added at the end saying an AI wrote it and a person approved it.</p>
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
      {d.error && <ErrorNote error={new Error(d.error)} />}
      {post.error ? <ErrorNote error={post.error} /> : null}
      <div className="flex flex-wrap gap-2">
        {comment && <Button size="sm" disabled={post.isPending || !text.trim() || !!d.pending} onClick={async () => { if (await confirm({ title: "Post this comment on the pull request?", message: "Everyone who can see the pull request will see it. It is posted with your GitHub connection.", confirmLabel: "Post comment" })) post.mutate(); }}>Post to the pull request</Button>}
        {d.status === "new" && <Button size="sm" variant="outline" disabled={mark.isPending} onClick={() => mark.mutate("read")}>Mark read</Button>}
        <Button size="sm" variant="outline" disabled={mark.isPending} onClick={() => mark.mutate("dismissed")}>Dismiss</Button>
      </div>
    </li>
  );
}

/** What the agent made, waiting for a person. A comment for a pull request is posted only when an admin approves it. */
export default function AgentInbox() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["agent-inbox"], queryFn: () => api<{ drafts: AgentDraft[] }>("/automations/inbox"), refetchInterval: 15000 });
  const done = () => { qc.invalidateQueries({ queryKey: ["agent-inbox"] }); qc.invalidateQueries({ queryKey: ["automations"] }); };
  return (
    <Card aria-label="Inbox">
      <CardTitle icon={Tray} right={<span className="caption-5 text-muted">{q.data?.drafts.length ?? 0} waiting</span>}>Inbox</CardTitle>
      <p className="mb-2 caption-5 text-muted">Nothing the agent writes goes to GitHub by itself. A comment waits here until you read it, edit it if you like, and post it.</p>
      {q.isLoading ? <Spinner /> : !q.data ? <ErrorNote error={q.error} /> : !q.data.drafts.length ? <Empty>Nothing is waiting for you.</Empty> : <ul className="divide-y divide-line">{q.data.drafts.map((d) => <Draft key={d.id} d={d} done={done} />)}</ul>}
    </Card>
  );
}
