import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, GitPullRequest, Wrench, X } from "lucide-react";
import { Badge, Button, Card, CardTitle, ErrorNote } from "@/components/ui";
import { useConfirm } from "@/components/feedback";
import { api, tokenStore } from "@/lib/api";
import { safeHref, timeAgo } from "@/lib/utils";

export interface FixProposal {
  id: string; title: string; rationale: string; files: string[]; diff: string; status: "pending" | "approved" | "rejected" | "failed";
  drafted_by: "llm" | "template"; model: string; tests: string[]; result: string; pr_url: string; created_at: string; target_public?: boolean | null;
  checks: { syntax_ok: boolean; lines_changed: number; notes: string[] };
}

const tone = (s: string) => (s === "approved" ? "green" : s === "pending" ? "amber" : s === "failed" ? "red" : "gray");

function Diff({ text }: { text: string }) {
  return (
    <pre aria-label="Proposed change" className="max-h-80 overflow-auto rounded-lg border border-line bg-slate-50 p-3 text-xs leading-5">
      {text.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "bg-emerald-100 text-emerald-900" : l.startsWith("-") && !l.startsWith("---") ? "bg-red-100 text-red-900" : l.startsWith("@@") ? "text-indigo-600" : ""}>{l || " "}</div>
      ))}
    </pre>
  );
}

/** A drafted code change for a confirmed root cause. A person reads the diff, then approves (a draft pull request) or rejects. */
export default function FixProposals({ investigationId, canAct }: { investigationId: string; canAct: boolean }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const key = ["fixes", investigationId];
  const list = useQuery({ queryKey: key, queryFn: () => api<FixProposal[]>(`/investigations/${investigationId}/fix-proposals`) });
  const propose = useMutation({ mutationFn: () => api<FixProposal>(`/investigations/${investigationId}/fix-proposals`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: key }) });
  const decide = useMutation({
    mutationFn: (v: { id: string; decision: "approve" | "reject"; confirmPublic?: boolean }) => api<FixProposal>(`/fix-proposals/${v.id}/decision`, { method: "POST", json: { decision: v.decision, ...(v.confirmPublic ? { confirm_public: true } : {}) } }),
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
  const patch = async (f: FixProposal) => {
    const r = await fetch(`/api/fix-proposals/${f.id}/patch`, { headers: { Authorization: `Bearer ${tokenStore.get()}` } });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob()); a.download = `repolens-fix-${f.id.slice(0, 8)}.patch`; a.click(); URL.revokeObjectURL(a.href);
  };
  const approve = async (f: FixProposal) => {
    const pub = f.target_public === true;
    const ok = await confirm({
      title: "Open a draft pull request?",
      message: `RepoLens will create the branch repolens/fix-${f.id.slice(0, 8)} and open a draft pull request changing ${f.files.join(", ")}. Nothing is merged; a person reviews it on GitHub.`,
      confirmLabel: "Open draft pull request",
      ...(pub ? { danger: true, warning: "This repository is PUBLIC. The branch, the code change and the explanation will be visible to everyone, and removing them later may not erase copies.", acknowledge: "I have read the change and the explanation, and I accept that they will be public." } : {}),
    });
    if (ok) decide.mutate({ id: f.id, decision: "approve", confirmPublic: pub });
  };
  const items = Array.isArray(list.data) ? list.data : [];

  return (
    <Card>
      <CardTitle right={canAct && <Button size="sm" variant="outline" disabled={propose.isPending} onClick={() => propose.mutate()}><Wrench className="h-3.5 w-3.5" />{propose.isPending ? "Drafting…" : "Propose a fix"}</Button>}>Suggested fix</CardTitle>
      <p className="text-xs text-muted">Drafted from the confirmed root cause, never run, and never merged. You review the change first.</p>
      <ErrorNote error={propose.error} />
      <ErrorNote error={decide.error} />
      {items.map((f) => (
        <div key={f.id} className="mt-4 border-t border-line pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{f.title}</span>
            <Badge tone={tone(f.status)}>{f.status}</Badge>
            {f.target_public === true && <Badge tone="red">public repository</Badge>}
            <span className="text-xs text-muted">{f.drafted_by === "llm" ? `written by ${f.model || "a model"}` : "simple template"} · {timeAgo(f.created_at)}</span>
          </div>
          <p className="mt-2 text-sm text-slate-700">{f.rationale}</p>
          <div className="mt-2 text-xs text-muted">{f.files.join(", ")} · {f.checks.lines_changed} lines · {f.checks.syntax_ok ? "syntax checked" : "syntax problem"}</div>
          {f.checks.notes.map((n) => <p key={n} className="text-xs text-red-600">{n}</p>)}
          <div className="mt-2"><Diff text={f.diff} /></div>
          {!!f.tests.length && <div className="mt-2 text-xs"><span className="font-medium">Tests to add or run:</span><ul className="list-disc pl-5">{f.tests.map((t) => <li key={t}>{t}</li>)}</ul></div>}
          {f.result && f.status !== "approved" && <p className="mt-2 text-xs text-red-600">{f.result}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => patch(f)}><Download className="h-3.5 w-3.5" />Download patch</Button>
            {f.status === "pending" && canAct && (<>
              <Button size="sm" disabled={decide.isPending || !f.checks.syntax_ok} onClick={() => approve(f)}><GitPullRequest className="h-3.5 w-3.5" />Approve and open draft PR</Button>
              <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate({ id: f.id, decision: "reject" })}><X className="h-3.5 w-3.5" />Reject</Button>
            </>)}
            {f.pr_url && safeHref(f.pr_url) && <a className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline" href={safeHref(f.pr_url)} target="_blank" rel="noreferrer"><ExternalLink className="h-3.5 w-3.5" />View pull request</a>}
          </div>
        </div>
      ))}
    </Card>
  );
}
