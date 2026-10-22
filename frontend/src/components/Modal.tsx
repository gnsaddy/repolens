import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

/** A centred card over a dimmed page: Escape or a click outside closes it; focus moves into it and returns afterwards. */
export default function Modal({ title, subtitle, onClose, children, icon }: { title: string; subtitle?: ReactNode; onClose: () => void; children: ReactNode; icon?: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>("input, select, textarea, button:not([aria-label='Close'])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="fixed inset-0 z-40 grid place-items-center overflow-auto bg-slate-900/40 p-4" onMouseDown={onClose}>
      <div ref={box} role="dialog" aria-modal="true" aria-labelledby="modal-title" className="w-full max-w-lg rounded-2xl bg-white shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3 border-b border-line p-5">
          {icon}
          <div className="min-w-0 flex-1"><h2 id="modal-title" className="text-lg font-semibold">{title}</h2>{subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}</div>
          <button aria-label="Close" onClick={onClose} className="rounded-md p-1 text-muted hover:bg-slate-100"><X className="h-5 w-5" /></button>
        </div>
        <div className="max-h-[70vh] overflow-auto p-5">{children}</div>
      </div>
    </div>
  );
}
