import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import JiraBatchPanel from "@/components/JiraBatchPanel";
import JiraIssueBrowser from "@/components/JiraIssueBrowser";
import JiraIssuePanel from "@/components/JiraIssuePanel";
import Modal from "@/components/Modal";
import { useToast } from "@/components/feedback";
import { useMutation } from "@tanstack/react-query";
import { api, type JiraBatch, type JiraInvestigateBatch } from "@/lib/api";

/** "Start from Jira": search tickets from the Investigations page, then analyse or investigate one or several. */
export default function JiraPicker({ canInvestigate, onClose }: { canInvestigate: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [batch, setBatch] = useState<JiraBatch | null>(null);
  const done = (r: JiraInvestigateBatch) => {
    qc.invalidateQueries({ queryKey: ["investigations"] });
    const n = r.started.length;
    toast(n ? `Started ${n} investigation${n === 1 ? "" : "s"}` : "Those tickets already have an investigation");
    onClose();
    if (n === 1 && !r.existing.length) nav(`/investigations/${r.started[0].investigation_id}`);
  };
  const all = useMutation({ mutationFn: () => api<JiraInvestigateBatch>("/jira/investigate-batch", { method: "POST", json: { keys: batch!.tickets.map((t) => t.key), repo_ids: [] } }), onSuccess: done });
  if (batch) return <JiraBatchPanel batch={batch} canInvestigate={canInvestigate} busy={all.isPending} onInvestigate={() => all.mutate()} onOpen={(k) => { setBatch(null); setOpenKey(k); }} onClose={() => setBatch(null)} />;
  if (openKey) return <JiraIssuePanel issueKey={openKey} canInvestigate={canInvestigate} onClose={() => setOpenKey(null)} />;
  return (
    <Modal wide title="Start from Jira" subtitle="Tick one or more tickets to analyse them or start investigations. Click a ticket to read it." onClose={onClose}>
      <JiraIssueBrowser compact canInvestigate={canInvestigate} onOpen={setOpenKey} onAnalyzed={setBatch} onInvestigated={done} />
    </Modal>
  );
}
