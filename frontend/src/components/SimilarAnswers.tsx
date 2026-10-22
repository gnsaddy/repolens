import { History } from "lucide-react";
import { Badge, Card, CardTitle } from "@/components/ui";
import type { SimilarAnswer } from "@/lib/api";

/** Questions already answered that are close to the one just asked, shown before the model's answer. Free: no tokens used. */
export default function SimilarAnswers({ items }: { items: SimilarAnswer[] | undefined }) {
  if (!items?.length) return null;
  return (
    <Card aria-label="Asked before">
      <CardTitle right={<Badge tone="green">no tokens used</Badge>}><History className="mr-1.5 inline h-4 w-4" />Asked before</CardTitle>
      <ul className="space-y-2">{items.map((s) => (
        <li key={s.id}><details className="rounded-lg border border-line p-2 text-sm">
          <summary className="cursor-pointer"><span className="font-medium">{s.asked}</span> <span className="text-xs text-muted">{Math.round(s.similarity * 100)}% similar · {s.age_hours < 1 ? "just now" : `${s.age_hours} h ago`}{s.hits ? ` · reused ${s.hits}×` : ""}</span></summary>
          <p className="mt-2 whitespace-pre-wrap leading-6">{s.answer}</p>
          {!!s.sources.length && <p className="mt-1 text-xs text-muted">Sources: {s.sources.join(", ")}</p>}
        </details></li>))}</ul>
    </Card>
  );
}
