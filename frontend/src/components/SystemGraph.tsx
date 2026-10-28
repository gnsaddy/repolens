import { useMemo } from "react";
import type { SystemLink, SystemRepo, SystemRisk } from "@/lib/api";

export type HeatMetric = "none" | keyof SystemRisk;
export const TRUST_COLOR = { fact: "#4f46e5", inferred: "#d97706", uncertain: "#94a3b8" } as const;
const W = 720, H = 440, CX = W / 2, CY = H / 2, NODE = 28;

/** White to red, so the repositories where something concentrates stand out. */
export const heatColor = (h: number) => `hsl(${Math.round(8 + (1 - h) * 30)}, ${Math.round(30 + h * 55)}%, ${Math.round(94 - h * 42)}%)`;
const clip = (s: string, n = 18) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** The repositories of a system on a ring, with an arrow from each one to those it depends on. Width is how many links
 *  there are; a dashed line is a link that was guessed rather than found in the code. */
export default function SystemGraph({ repos, links, metric, selected, onRepo, onLink }: {
  repos: SystemRepo[]; links: SystemLink[]; metric: HeatMetric; selected: string | null;
  onRepo: (id: string) => void; onLink: (l: SystemLink) => void;
}) {
  const pos = useMemo(() => {
    const r = Math.min(W, H) / 2 - 70;
    return new Map(repos.map((x, i) => {
      const a = repos.length === 1 ? 0 : (2 * Math.PI * i) / repos.length - Math.PI / 2;
      return [x.id, repos.length === 1 ? { x: CX, y: CY } : { x: CX + r * 1.45 * Math.cos(a), y: CY + r * Math.sin(a) }];
    }));
  }, [repos]);
  const name = new Map(repos.map((r) => [r.id, r.name]));
  const linked = (id: string) => selected === id || links.some((l) => (l.src === id && l.dst === selected) || (l.dst === id && l.src === selected));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="How the repositories depend on each other" className="w-full rounded-lg border border-line bg-surface">
      <defs>{(Object.keys(TRUST_COLOR) as (keyof typeof TRUST_COLOR)[]).map((t) => (
        <marker key={t} id={`arrow-${t}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={TRUST_COLOR[t]} /></marker>))}</defs>
      {links.map((l) => {
        const a = pos.get(l.src), b = pos.get(l.dst);
        if (!a || !b) return null;
        const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;
        const s = { x: a.x + ux * NODE, y: a.y + uy * NODE }, e = { x: b.x - ux * (NODE + 6), y: b.y - uy * (NODE + 6) };
        const bend = 26; // links in opposite directions curve apart instead of lying on one line
        const c = { x: (s.x + e.x) / 2 - uy * bend, y: (s.y + e.y) / 2 + ux * bend };
        const on = selected === l.src || selected === l.dst || selected === `${l.src}>${l.dst}`;
        return (
          <g key={`${l.src}>${l.dst}`} role="button" tabIndex={0} aria-label={`${name.get(l.src)} depends on ${name.get(l.dst)}: ${l.strong + l.http + l.imports + l.declared + l.infra} links, ${l.weak} by name only, ${l.trust}`} className="cursor-pointer"
            onClick={() => onLink(l)} onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") onLink(l); }}>
            <path d={`M${s.x},${s.y} Q${c.x},${c.y} ${e.x},${e.y}`} fill="none" stroke="transparent" strokeWidth={14} />
            <path d={`M${s.x},${s.y} Q${c.x},${c.y} ${e.x},${e.y}`} fill="none" stroke={TRUST_COLOR[l.trust]} strokeWidth={1.2 + Math.min(5, Math.log2(l.strong + l.http + l.imports + l.declared + l.infra + 1))}
              strokeDasharray={l.trust === "fact" ? undefined : "6 4"} opacity={selected && !on ? 0.2 : 0.9} markerEnd={`url(#arrow-${l.trust})`} />
            <text x={c.x / 2 + (s.x + e.x) / 4} y={c.y / 2 + (s.y + e.y) / 4 - 4} textAnchor="middle" fontSize="10" fill="currentColor" className="text-muted">{l.strong + l.http + l.imports + l.declared + l.infra > 0 ? l.strong + l.http + l.imports + l.declared + l.infra : `~${l.weak}`}</text>
          </g>);
      })}
      {repos.map((r) => {
        const p = pos.get(r.id)!;
        const h = metric === "none" ? 0 : r.heat[metric];
        return (
          <g key={r.id} role="button" tabIndex={0} aria-label={`Repository ${r.name}`} aria-pressed={selected === r.id} className="cursor-pointer" opacity={selected && !linked(r.id) ? 0.35 : 1}
            onClick={() => onRepo(r.id)} onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") onRepo(r.id); }}>
            <circle cx={p.x} cy={p.y} r={NODE} fill={metric === "none" ? (r.source === "docs" ? "#e0f2fe" : r.role === "infrastructure" ? "#ccfbf1" : "#eef2ff") : heatColor(h)} stroke={selected === r.id ? "#4f46e5" : "#94a3b8"} strokeWidth={selected === r.id ? 3 : 1.5} />
            <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize="11" fontWeight="600" fill="#334155">{r.name.slice(0, 2).toUpperCase()}</text>
            <text x={p.x} y={p.y + NODE + 14} textAnchor="middle" fontSize="11" fill="currentColor">{clip(r.name)}</text>
            {r.role === "infrastructure" && <text x={p.x} y={p.y + NODE + 26} textAnchor="middle" fontSize="9" fill="#0f766e">infrastructure</text>}
          </g>);
      })}
    </svg>
  );
}
