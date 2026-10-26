import { Info } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/** A small (i) button that explains a setting. Shows on hover, keyboard focus or click; Escape or an outside click closes it. */
export default function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  const shown = open || pinned;
  useEffect(() => {
    if (!shown) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); setPinned(false); } };
    const click = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) { setOpen(false); setPinned(false); } };
    window.addEventListener("keydown", key); window.addEventListener("mousedown", click);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("mousedown", click); };
  }, [shown]);
  return (
    <span ref={box} className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" aria-label={label} aria-expanded={shown} aria-describedby={shown ? id : undefined} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={() => setPinned((v) => !v)} className="rounded-full p-0.5 text-muted hover:text-brand"><Info className="h-4 w-4" /></button>
      {shown && <span id={id} role="tooltip" className="absolute right-0 top-full z-30 mt-1 w-72 rounded-lg border border-line bg-white p-3 text-left text-xs leading-relaxed text-ink shadow-lg">{children}</span>}
    </span>
  );
}
