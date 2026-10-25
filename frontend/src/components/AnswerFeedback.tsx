import { useMutation } from "@tanstack/react-query";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { Button, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/** "Was this helpful?": the answers people mark down become the next evaluation questions. */
export default function AnswerFeedback({ query, targetId, fromMemory }: { query: string; targetId: string; fromMemory?: boolean }) {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");
  const send = useMutation({
    mutationFn: (v: { rating: "up" | "down"; note?: string }) => api("/feedback", { method: "POST", json: { kind: fromMemory ? "memory" : "answer", rating: v.rating, target_id: targetId, query, note: v.note ?? "", meta: { from_memory: !!fromMemory } } }),
    onSuccess: (_r, v) => { setRating(v.rating); setAsking(false); },
  });
  if (rating && !asking) return <p role="status" className="mt-3 text-xs text-muted">Thanks, noted{rating === "down" ? ". It will help improve answers." : "."}</p>;
  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="flex items-center gap-2 text-xs text-muted">
        <span>Was this helpful?</span>
        <button type="button" aria-label="Helpful" disabled={send.isPending} onClick={() => send.mutate({ rating: "up" })} className={cn("rounded p-1 hover:bg-emerald-50 hover:text-emerald-700")}><ThumbsUp className="h-4 w-4" /></button>
        <button type="button" aria-label="Not helpful" disabled={send.isPending} onClick={() => setAsking(true)} className="rounded p-1 hover:bg-red-50 hover:text-red-700"><ThumbsDown className="h-4 w-4" /></button>
      </div>
      {asking && (
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); send.mutate({ rating: "down", note }); }}>
          <Input aria-label="What was wrong?" autoFocus placeholder="What was missing or wrong? (optional)" value={note} onChange={(e) => setNote(e.target.value)} className="h-8 text-xs" />
          <Button size="sm" disabled={send.isPending}>Send</Button><Button size="sm" type="button" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>
        </form>)}
    </div>
  );
}
