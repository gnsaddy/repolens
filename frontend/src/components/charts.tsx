import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { cn } from "@/lib/utils";

/** A thin line chart with no axes, for "how has this moved lately". */
export function Spark({ data, color = "#4f46e5", format, height = 44, max }: { data: { v: number | null }[]; color?: string; format?: (n: number) => string; height?: number; max?: number }) {
  const pts = data.map((d, i) => ({ i, v: d.v ?? 0 }));
  return (
    <div style={{ height }} aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={pts} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <YAxis hide domain={[0, max ?? "auto"]} />
          <Tooltip content={({ active, payload }) => (active && payload?.length ? <div className="rounded bg-slate-900 px-1.5 py-0.5 text-xs text-white">{format ? format(Number(payload[0].value)) : String(payload[0].value)}</div> : null)} />
          <Area type="monotone" dataKey="v" stroke={color} fill={color} fillOpacity={0.15} strokeWidth={1.5} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** A horizontal fill bar that turns amber then red as it fills. */
export function Meter({ value, label, warn = 70, bad = 90 }: { value: number | null; label: string; warn?: number; bad?: number }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div role="meter" aria-label={label} aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100} className="h-2 overflow-hidden rounded-full bg-slate-100">
      <div className={cn("h-full rounded-full transition-all", v >= bad ? "bg-red-500" : v >= warn ? "bg-amber-500" : "bg-emerald-500")} style={{ width: `${v}%` }} />
    </div>
  );
}
