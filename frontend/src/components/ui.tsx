import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:opacity-50 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        primary: "bg-brand text-white hover:bg-brand-600",
        outline: "border border-line bg-white text-ink hover:bg-slate-50",
        ghost: "text-muted hover:bg-slate-100",
        danger: "bg-red-600 text-white hover:bg-red-700",
      },
      size: { sm: "h-8 px-3", md: "h-9 px-4" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);
export function Button({ className, variant, size, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button>) {
  return <button className={cn(button({ variant, size }), className)} {...p} />;
}

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-line bg-surface p-5 shadow-sm", className)} {...p} />;
}
export function CardTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return <div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold">{children}</h3>{right}</div>;
}

const badge = cva("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", {
  variants: {
    tone: {
      gray: "bg-slate-100 text-slate-700", green: "bg-emerald-50 text-emerald-700",
      blue: "bg-blue-50 text-blue-700", amber: "bg-amber-50 text-amber-700", red: "bg-red-50 text-red-700",
      brand: "bg-indigo-50 text-indigo-700",
    },
  },
  defaultVariants: { tone: "gray" },
});
export function Badge({ tone, className, ...p }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) {
  return <span className={cn(badge({ tone }), className)} {...p} />;
}

export const statusTone = (s: string) =>
  ({ completed: "green", ready: "green", supported: "green", confirmed: "green", running: "blue", indexing: "blue",
    queued: "amber", pending: "amber", unverified: "amber", unconfirmed: "amber", failed: "red", error: "red",
    rejected: "red" })[s] as "green" | "blue" | "amber" | "red" | undefined ?? "gray";

export function Input({ className, ...p }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn("h-9 w-full rounded-md border border-line bg-white px-3 text-sm outline-none placeholder:text-slate-400 focus:border-brand focus:ring-2 focus:ring-indigo-100", className)} {...p} />;
}
export function Textarea({ className, ...p }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn("w-full rounded-md border border-line bg-white p-3 text-sm outline-none placeholder:text-slate-400 focus:border-brand focus:ring-2 focus:ring-indigo-100", className)} {...p} />;
}
export function Label({ children }: { children: ReactNode }) {
  return <span className="mb-1 block text-xs font-medium text-muted">{children}</span>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

export function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 0.75 ? "bg-emerald-500" : value >= 0.5 ? "bg-brand" : "bg-amber-500";
  return (
    <div className="flex items-center gap-3" role="meter" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Confidence">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100"><div className={cn("h-full rounded-full", color)} style={{ width: `${Math.round(value * 100)}%` }} /></div>
      <span className="w-10 text-right text-sm font-semibold">{Math.round(value * 100)}%</span>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="text-xl font-semibold tracking-tight">{title}</h1>{subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}</div>
      {actions}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line p-8 text-center text-sm text-muted">{children}</div>;
}
export function ErrorNote({ error }: { error: unknown }) {
  return error ? <div role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{String((error as Error).message ?? error)}</div> : null;
}
export function Spinner() {
  return <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand" aria-label="Loading" />;
}

export function CodeBlock({ code, startLine = 1, highlight }: { code: string; startLine?: number; highlight?: [number, number] }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-line bg-slate-50 py-2 font-mono text-xs leading-5">
      {code.split("\n").map((l, i) => {
        const n = startLine + i;
        const hot = highlight && n >= highlight[0] && n <= highlight[1];
        return (
          <div key={i} className={cn("flex px-3", hot && "bg-amber-100/70")}>
            <span className="mr-4 w-8 shrink-0 select-none text-right text-slate-400">{n}</span>
            <code className="whitespace-pre">{l || " "}</code>
          </div>
        );
      })}
    </pre>
  );
}
