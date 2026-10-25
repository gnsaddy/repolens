import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import JiraBatchPanel from "@/components/JiraBatchPanel";
import JiraIssueBrowser from "@/components/JiraIssueBrowser";
import JiraIssuePanel from "@/components/JiraIssuePanel";
import { useToast } from "@/components/feedback";
import { Card, PageHeader } from "@/components/ui";
import { api, type JiraBatch, type JiraConnection, type JiraInvestigateBatch } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";

export default function JiraPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const conn = useQuery({ queryKey: ["jira-conn"], queryFn: () => api<JiraConnection | null>("/jira/connection") });
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [batch, setBatch] = useState<JiraBatch | null>(null);
  const done = (r: JiraInvestigateBatch) => {
    qc.invalidateQueries({ queryKey: ["jira-issues"] });
    const n = r.started.length;
    toast(n ? `Started ${n} investigation${n === 1 ? "" : "s"}${r.existing.length ? `; ${r.existing.length} already had one` : ""}` : "Those tickets already have an investigation");
    setBatch(null);
    if (n === 1 && !r.existing.length) nav(`/investigations/${r.started[0].investigation_id}`); else nav("/investigations");
  };
  const investigateAll = useMutation({ mutationFn: () => api<JiraInvestigateBatch>("/jira/investigate-batch", { method: "POST", json: { keys: batch!.tickets.map((t) => t.key), repo_ids: [] } }), onSuccess: done });

  return (
    <div>
      <PageHeader title="Jira" subtitle="Find tickets, read them with their comments, analyse one or several together, and investigate the cause in your code and docs." />
      {!conn.isLoading && !conn.data ? (
        <Card className="mx-auto max-w-xl text-center">
          <h2 className="text-base font-semibold">Connect Jira first</h2>
          <p className="mt-1 text-sm text-muted">Add your Jira site and an API token in Settings, then come back to browse tickets, investigate them and publish approved reports.</p>
          <Link to="/settings" className="mt-4 inline-flex h-9 items-center rounded-md bg-brand px-4 text-sm font-medium text-white hover:bg-brand-600">Open Settings</Link>
        </Card>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted">Connected to <span className="font-medium text-ink">{conn.data?.base_url}</span>. <Link to="/settings" className="text-brand hover:underline">Manage connection</Link></p>
          {conn.data && <JiraIssueBrowser canInvestigate={canWrite(user)} onOpen={setOpenKey} onAnalyzed={setBatch} onInvestigated={done} />}
        </div>)}
      {openKey && <JiraIssuePanel issueKey={openKey} canInvestigate={canWrite(user)} onClose={() => setOpenKey(null)} />}
      {batch && <JiraBatchPanel batch={batch} canInvestigate={canWrite(user)} busy={investigateAll.isPending} onInvestigate={() => investigateAll.mutate()} onOpen={(k) => { setBatch(null); setOpenKey(k); }} onClose={() => setBatch(null)} />}
    </div>
  );
}
