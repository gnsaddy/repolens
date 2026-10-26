import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui";
import type { Section } from "@/lib/sections";

/** Previous, a picker and Next for a document shown one section at a time. */
export default function SectionNav({ sections, index, onIndex, onAll, label = "Section" }: { sections: Section[]; index: number; onIndex: (i: number) => void; onAll?: () => void; label?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Button size="sm" variant="outline" disabled={index === 0} onClick={() => onIndex(index - 1)}><ChevronLeft className="h-4 w-4" />Previous</Button>
      <select aria-label={label} value={index} onChange={(e) => onIndex(Number(e.target.value))} className="h-8 max-w-[16rem] truncate rounded-md border border-line bg-white px-2 text-sm">
        {sections.map((s, i) => <option key={i} value={i}>{i + 1}. {s.title}</option>)}
      </select>
      <Button size="sm" variant="outline" disabled={index >= sections.length - 1} onClick={() => onIndex(index + 1)}>Next<ChevronRight className="h-4 w-4" /></Button>
      <span className="text-xs text-muted" aria-label="Position">{index + 1} of {sections.length}</span>
      {onAll && <button className="ml-auto text-xs text-brand hover:underline" onClick={onAll}>Show all {sections.length} sections</button>}
    </div>
  );
}
