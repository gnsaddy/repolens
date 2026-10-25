import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export const fmtTokens = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(2)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);

const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usdSmall = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });
/** US dollars: $1,234.57 for amounts from $1, and up to four decimals below $1 so small costs stay visible ($0.0105). */
export const fmtCost = (n: number): string => (Math.abs(n) >= 1 ? usd2 : usdSmall).format(n || 0);
export const fmtBytes = (n: number | null | undefined): string => {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
};
export const fmtRate = (bps: number | null | undefined): string => (bps == null ? "—" : `${fmtBytes(bps)}/s`);
export const fmtDuration = (s: number): string => {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
};

export const pct = (n: number | null | undefined): string => (n == null ? "—" : `${Math.round(n * 100)}%`);

/** Only http(s) addresses become links, so a stored `javascript:` or `data:` URL can never run when clicked. */
export const safeHref = (url?: string | null): string | undefined => (url && /^https?:\/\//i.test(url.trim()) ? url.trim() : undefined);

/** Points for the "tokens per day" chart. The server sends one `tokens` figure per day; older shapes sent input and output
 *  separately. A missing number must never reach the chart as NaN. */
export function dailyTokenSeries(daily: { date: string; tokens?: number; input_tokens?: number; output_tokens?: number }[]) {
  return (Array.isArray(daily) ? daily : []).map((d) => {
    const n = d.tokens ?? (d.input_tokens ?? 0) + (d.output_tokens ?? 0);
    return { date: d.date.slice(5), tokens: Number.isFinite(n) ? n : 0 };
  });
}
