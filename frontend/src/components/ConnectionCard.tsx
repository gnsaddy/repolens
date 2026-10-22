import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export const TINTS = {
  indigo: "bg-indigo-50 text-indigo-600 ring-indigo-100", slate: "bg-slate-100 text-slate-700 ring-slate-200", blue: "bg-blue-50 text-blue-600 ring-blue-100",
  emerald: "bg-emerald-50 text-emerald-600 ring-emerald-100", amber: "bg-amber-50 text-amber-600 ring-amber-100", violet: "bg-violet-50 text-violet-600 ring-violet-100",
} as const;

export function StatusPill({ on, onLabel = "Connected", offLabel = "Not connected" }: { on: boolean; onLabel?: string; offLabel?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium", on ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600")}>
      <span className={cn("h-1.5 w-1.5 rounded-full", on ? "bg-emerald-500" : "bg-slate-400")} aria-hidden />{on ? onLabel : offLabel}</span>
  );
}

/** One integration or setting: icon tile, title, description and live status on top, content below, optional action bar. */
export function ConnectionCard({ icon: Icon, tint = "indigo", title, description, status, children, footer, id }: {
  icon: LucideIcon; tint?: keyof typeof TINTS; title: string; description: ReactNode; status?: ReactNode; children?: ReactNode; footer?: ReactNode; id?: string;
}) {
  return (
    <section id={id} aria-label={title} className="overflow-hidden rounded-2xl border border-line bg-surface shadow-sm">
      <header className="flex items-start gap-4 p-5">
        <span className={cn("grid h-11 w-11 shrink-0 place-items-center rounded-xl ring-1", TINTS[tint])}><Icon className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1"><h3 className="text-base font-semibold leading-6">{title}</h3><p className="mt-0.5 text-sm text-muted">{description}</p></div>
        {status}
      </header>
      {children && <div className="border-t border-line/70 px-5 py-4">{children}</div>}
      {footer && <footer className="flex flex-wrap items-center gap-2 border-t border-line/70 bg-slate-50/70 px-5 py-3">{footer}</footer>}
    </section>
  );
}

export const selectCls = "h-9 w-full rounded-lg border border-line bg-white px-2.5 text-sm shadow-sm focus-visible:outline-2 focus-visible:outline-brand";
