import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

export const NODE_COLORS: Record<string, string> = {
  file: "#64748b", doc: "#0ea5e9", section: "#38bdf8", function: "#4f46e5", class: "#7c3aed", commit: "#f59e0b", pr: "#10b981",
};
export const REL_COLORS: Record<string, string> = {
  CALLS: "#4f46e5", IMPORTS: "#64748b", MENTIONS: "#0ea5e9", LINKS_TO: "#06b6d4", TESTS: "#10b981",
  MODIFIES: "#f59e0b", RELATED_TO: "#ec4899", CONTAINS: "#cbd5e1", INCLUDES: "#10b981",
};

/** blue (cold) -> yellow -> red (hot) */
export function heatColor(t: number): string {
  const stops = [[59, 130, 246], [250, 204, 21], [239, 68, 68]];
  const x = Math.max(0, Math.min(1, t)) * 2, i = Math.min(1, Math.floor(x)), f = x - i;
  const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
export const HEAT_GRADIENT = "linear-gradient(90deg, rgb(59,130,246), rgb(250,204,21), rgb(239,68,68))";
const DIRECTED = new Set(["CALLS", "IMPORTS", "TESTS", "MENTIONS", "LINKS_TO", "MODIFIES", "INCLUDES"]);

interface P { x: number; y: number; vx: number; vy: number }
const W = 900, H = 600;

/** Small dependency-free force layout (repulsion + springs + gravity), run to rest then rendered statically. */
export function layout(nodes: GraphNode[], edges: GraphEdge[], seedPositions: Record<string, P> = {}): Record<string, P> {
  const pos: Record<string, P> = {};
  nodes.forEach((n, i) => {
    const a = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
    pos[n.id] = seedPositions[n.id] ?? { x: W / 2 + Math.cos(a) * 220 + (i % 7), y: H / 2 + Math.sin(a) * 160 + (i % 5), vx: 0, vy: 0 };
  });
  const links = edges.filter((e) => pos[e.source] && pos[e.target]);
  const iters = nodes.length > 150 ? 160 : 260;
  for (let it = 0; it < iters; it++) {
    const cool = 1 - it / iters;
    for (let i = 0; i < nodes.length; i++) {
      const a = pos[nodes[i].id];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = pos[nodes[j].id];
        let dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy + 0.01;
        if (d2 > 90000) continue;
        const f = 2600 / d2;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      }
    }
    for (const e of links) {
      const a = pos[e.source], b = pos[e.target];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = e.rel === "CONTAINS" ? 50 : 90;
      const f = (d - rest) * 0.02 * (0.5 + e.weight / 2);
      a.vx += (dx / d) * f; a.vy += (dy / d) * f; b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    for (const n of nodes) {
      const p = pos[n.id];
      p.vx += (W / 2 - p.x) * 0.004; p.vy += (H / 2 - p.y) * 0.004;
      p.x += Math.max(-20, Math.min(20, p.vx)) * cool; p.y += Math.max(-20, Math.min(20, p.vy)) * cool;
      p.vx *= 0.6; p.vy *= 0.6;
    }
  }
  return pos;
}

export function fitView(nodes: GraphNode[], pos: Record<string, P>): { x: number; y: number; k: number } {
  const pts = nodes.map((n) => pos[n.id]).filter(Boolean);
  if (!pts.length) return { x: 0, y: 0, k: 1 };
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const w = Math.max(maxX - minX, 80), h = Math.max(maxY - minY, 80);
  const k = Math.max(0.4, Math.min((W * 0.88) / w, (H * 0.86) / h, 2.2));
  return { k, x: W / 2 - ((minX + maxX) / 2) * k, y: H / 2 - ((minY + maxY) / 2) * k };
}

interface Props {
  nodes: GraphNode[]; edges: GraphEdge[]; selectedId: string | null; highlight?: Set<string>;
  onSelect: (id: string) => void; heat?: Record<string, number>;
}

export default function ForceGraph({ nodes, edges, selectedId, highlight, onSelect, heat }: Props) {
  const posRef = useRef<Record<string, P>>({});
  const pos = useMemo(() => {
    posRef.current = layout(nodes, edges, posRef.current);
    return posRef.current;
  }, [nodes, edges]);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const svg = useRef<SVGSVGElement>(null);

  // Fit the layout to the canvas whenever the node set changes (pan/zoom afterwards is the user's).
  const fitKey = nodes.map((n) => n.id).join("|");
  useEffect(() => setView(fitView(nodes, pos)), [fitKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const focus = hover ?? selectedId;
  const adj = useMemo(() => {
    const m = new Map<string, Set<string>>();
    edges.forEach((e) => {
      (m.get(e.source) ?? m.set(e.source, new Set()).get(e.source)!).add(e.target);
      (m.get(e.target) ?? m.set(e.target, new Set()).get(e.target)!).add(e.source);
    });
    return m;
  }, [edges]);
  const maxDeg = Math.max(1, ...nodes.map((n) => n.degree));
  const radius = (n: GraphNode) => 5 + 9 * Math.sqrt(n.degree / maxDeg);
  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const near = (id: string) => !focus || id === focus || adj.get(focus)?.has(id);

  return (
    <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Knowledge graph" className="h-[34rem] w-full cursor-grab rounded-lg border border-line bg-white select-none"
      onWheel={(e) => { e.preventDefault(); setView((v) => ({ ...v, k: Math.max(0.4, Math.min(3, v.k * (e.deltaY < 0 ? 1.1 : 0.9))) })); }}
      onMouseDown={(e) => { drag.current = { x: e.clientX, y: e.clientY }; }}
      onMouseMove={(e) => { if (drag.current) { const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y; drag.current = { x: e.clientX, y: e.clientY }; setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy })); } }}
      onMouseUp={() => { drag.current = null; }} onMouseLeave={() => { drag.current = null; }}>
      <defs>
        {Object.entries(REL_COLORS).map(([rel, c]) => (
          <marker key={rel} id={`arrow-${rel}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={c} /></marker>))}
      </defs>
      <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
        {edges.map((e) => {
          const a = pos[e.source], b = pos[e.target];
          if (!a || !b) return null;
          const on = highlight?.has(e.id) || (focus && (e.source === focus || e.target === focus));
          const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
          const tn = nodeById.get(e.target);
          const end = DIRECTED.has(e.rel) && tn ? radius(tn) + 2 : 0; // stop at the node edge so the arrowhead is visible
          return (
            <g key={e.id}>
              <line x1={a.x} y1={a.y} x2={b.x - (dx / len) * end} y2={b.y - (dy / len) * end} stroke={REL_COLORS[e.rel] ?? "#94a3b8"}
                markerEnd={DIRECTED.has(e.rel) && (on || !focus) ? `url(#arrow-${e.rel})` : undefined}
                strokeWidth={on ? 2.5 : 1} strokeDasharray={e.provenance === "semantic" ? "4 3" : undefined}
                opacity={on ? 0.95 : focus ? 0.12 : e.rel === "CONTAINS" ? 0.35 : 0.55}><title>{`${e.rel} (${e.provenance}, ${e.weight})\n${e.evidence}`}</title></line>
              {on && e.rel !== "CONTAINS" && <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 3} textAnchor="middle" fontSize={9} fill={REL_COLORS[e.rel] ?? "#475569"} style={{ stroke: "var(--color-surface)" }} strokeWidth={3} paintOrder="stroke">{e.rel.toLowerCase().replace("_", " ")}</text>}
            </g>);
        })}
        {nodes.map((n) => {
          const p = pos[n.id];
          if (!p) return null;
          const sel = n.id === selectedId;
          const showLabel = sel || n.id === hover || n.degree >= maxDeg * 0.35 || nodes.length < 25 || (focus && adj.get(focus)?.has(n.id));
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y})`} opacity={near(n.id) ? 1 : 0.25} className="cursor-pointer"
              onClick={(ev) => { ev.stopPropagation(); onSelect(n.id); }} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
              onMouseDown={(ev) => ev.stopPropagation()} role="button" aria-label={`${n.type} ${n.qualname || n.name}`}>
              <circle r={radius(n)} fill={heat ? (n.id in heat ? heatColor(heat[n.id]) : "#e2e8f0") : (NODE_COLORS[n.type] ?? "#94a3b8")} style={{ stroke: sel ? "var(--color-ink)" : highlight?.has(n.id) ? "#ec4899" : "var(--color-surface)" }} strokeWidth={sel || highlight?.has(n.id) ? 3 : 1.5} />
              <title>{`${n.type} ${n.qualname || n.name}\n${n.degree} connections${heat && n.id in heat ? `\nheat ${Math.round(heat[n.id] * 100)}%` : ""}`}</title>
              {showLabel && <text y={-radius(n) - 4} textAnchor="middle" fontSize={10} style={{ fill: "var(--color-ink)", stroke: "var(--color-surface)" }} strokeWidth={3} paintOrder="stroke">{n.name.length > 28 ? n.name.slice(0, 27) + "…" : n.name}</text>}
            </g>
          );
        })}
      </g>
    </svg>
  );
}
