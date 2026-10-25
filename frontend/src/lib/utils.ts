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

export const fmtCost = (n: number): string => `$${n.toFixed(n < 1 ? 4 : 2)}`;

export const pct = (n: number | null | undefined): string => (n == null ? "—" : `${Math.round(n * 100)}%`);

/** Only http(s) addresses become links, so a stored `javascript:` or `data:` URL can never run when clicked. */
export const safeHref = (url?: string | null): string | undefined => (url && /^https?:\/\//i.test(url.trim()) ? url.trim() : undefined);
