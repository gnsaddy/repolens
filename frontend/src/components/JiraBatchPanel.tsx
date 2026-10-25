import { AlertTriangle, Copy, Microscope } from "lucide-react";
import { Link } from "react-router-dom";
import Modal from "@/components/Modal";
import { Badge, Button } from "@/components/ui";
import type { JiraBatch, JiraShared } from "@/lib/api";

function Shared({ title, rows, tone }: { title: string; rows: JiraShared[]; tone: "red" | "blue" | "brand" | "gray" }) {
  if (!rows.length) return null;
  return (
    <section><h4 className="mb-1 text-xs font-medium text-muted">{title}</h4>
      <ul className="space-y-1 text-sm">{rows.map((r) => <li key={r.name} className="flex flex-wrap items-center gap-2"><Badge tone={tone}>{r.name}</Badge><span className="text-xs text-muted">in {r.keys.join(", ")}</span></li>)}</ul></section>
  );
}

/** What several tickets have in common, so one investigation can serve them all. */
export default function JiraBatchPanel({ batch, canInvestigate, busy, onInvestigate, onOpen, onClose }: {
  batch: JiraBatch; canInvestigate: boolean; busy: boolean; onInvestigate: () => void; onOpen: (key: string) => void; onClose: () => void;
}) {
  const c = batch.common;
  const nothingShared = !c.errors.length && !c.files.length && !c.symbols.length && !c.components.length && !c.possible_duplicates.length;
  return (
    <Modal wide title={`Analysis of ${batch.tickets.length} tickets`} subtitle="Each ticket was read with its comments; below is what they share." onClose={onClose}>
      <div className="space-y-4" aria-label="Batch analysis">
        {c.possible_duplicates.length > 0 && (
          <div role="status" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
            <div className="mb-1 flex items-center gap-1.5 font-medium"><Copy className="h-4 w-4" />Possible duplicates</div>
            <ul className="space-y-0.5">{c.possible_duplicates.map((d) => <li key={d.keys.join()}><b>{d.keys.join(" and ")}</b>: {d.why}</li>)}</ul>
            <p className="mt-1 text-xs">Investigate one of each pair; the other may close with the same cause.</p>
          </div>)}
        {nothingShared ? <p className="text-sm text-muted">These tickets share no error, file, symbol or component, so they are probably unrelated and are best investigated separately.</p> : (<>
          <Shared title="Same error in several tickets" rows={c.errors} tone="red" />
          <Shared title="Same file mentioned" rows={c.files} tone="blue" />
          <Shared title="Same code symbol mentioned" rows={c.symbols} tone="brand" />
          <Shared title="Same component" rows={c.components} tone="gray" />
        </>)}
        <section><h4 className="mb-1 text-xs font-medium text-muted">Each ticket</h4>
          <ul className="divide-y divide-line rounded-lg border border-line">{batch.tickets.map((t) => (
            <li key={t.key} className="p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2"><button className="font-medium text-brand hover:underline" onClick={() => onOpen(t.key)}>{t.key}</button><span className="min-w-0 flex-1 truncate">{t.summary}</span>
                {t.investigation_id && <Link className="text-xs text-brand hover:underline" to={`/investigations/${t.investigation_id}`}>View RCA</Link>}</div>
              <div className="mt-1 flex flex-wrap gap-1.5">{t.signals.errors.map((e) => <Badge key={e} tone="red">{e}</Badge>)}{t.signals.files.slice(0, 3).map((f) => <Badge key={f} tone="blue">{f}</Badge>)}</div>
              {t.discussion.cause[0] && <p className="mt-1 text-xs text-muted"><b>Proposed cause:</b> {t.discussion.cause[0].text}</p>}
              {t.flags.slice(0, 2).map((f) => <p key={f} className="mt-0.5 flex items-start gap-1 text-xs text-amber-800"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{f}</p>)}
            </li>))}</ul></section>
        {batch.related.length > 0 && <section><h4 className="mb-1 text-xs font-medium text-muted">Related in your docs and code</h4>
          <ul className="space-y-1 text-sm">{batch.related.map((r, i) => <li key={i}><Badge tone={r.kind === "doc" ? "green" : "gray"}>{r.kind}</Badge> {r.node_id ? <Link className="text-brand hover:underline" to={`/knowledge?tab=graph&node=${r.node_id}`}>{r.title}</Link> : r.title} <span className="text-xs text-muted">{r.repo}:{r.file}</span></li>)}</ul></section>}
        {c.attention.length > 0 && <section><h4 className="mb-1 text-xs font-medium text-muted">Gaps worth closing</h4><ul className="list-disc space-y-0.5 pl-5 text-xs text-muted">{c.attention.map((a) => <li key={a}>{a}</li>)}</ul></section>}
        <div className="flex justify-end gap-2 border-t border-line pt-3">{canInvestigate && <Button disabled={busy} onClick={onInvestigate}><Microscope className="h-4 w-4" />{busy ? "Starting…" : `Investigate all ${batch.tickets.length}`}</Button>}<Button variant="outline" onClick={onClose}>Close</Button></div>
      </div>
    </Modal>
  );
}
