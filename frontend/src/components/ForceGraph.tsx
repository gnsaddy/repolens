import { Download, Expand, Maximize2, Minimize2, Minus, Pause, Play, Plus, ScanSearch } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/api";

export const NODE_COLORS: Record<string, string> = {
  file: "#64748b", doc: "#0ea5e9", section: "#38bdf8", function: "#4f46e5", class: "#7c3aed", commit: "#f59e0b", pr: "#10b981",
};
export const COMMUNITY_COLORS = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16", "#f97316"];
export const communityColor = (c: number | null | undefined) => (c == null ? "#cbd5e1" : COMMUNITY_COLORS[c % COMMUNITY_COLORS.length]);
/** Solid = found directly in code or links, dashed = inferred from words or meaning, dotted = a weak guess. */
export const trustDash = (t?: string) => (t === "inferred" ? "5 3" : t === "uncertain" ? "1.5 3" : undefined);
export const REL_COLORS: Record<string, string> = {
  CALLS: "#4f46e5", IMPORTS: "#64748b", MENTIONS: "#0ea5e9", LINKS_TO: "#06b6d4", TESTS: "#10b981",
  MODIFIES: "#f59e0b", RELATED_TO: "#ec4899", CONTAINS: "#cbd5e1", INCLUDES: "#10b981", HTTP: "#0d9488", USES: "#c026d3", DECLARES: "#0891b2",
  REFERENCES: "#64748b", DEPLOYS: "#ea580c", CONNECTS: "#0ea5e9", USES_QUEUE: "#a855f7", CONFIGURED: "#65a30d",
};

/** blue (cold) -> yellow -> red (hot) */
export function heatColor(t: number): string {
  const stops = [[59, 130, 246], [250, 204, 21], [239, 68, 68]];
  const x = Math.max(0, Math.min(1, t)) * 2, i = Math.min(1, Math.floor(x)), f = x - i;
  const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
export const HEAT_GRADIENT = "linear-gradient(90deg, rgb(59,130,246), rgb(250,204,21), rgb(239,68,68))";
const DIRECTED = new Set(["CALLS", "IMPORTS", "TESTS", "MENTIONS", "LINKS_TO", "MODIFIES", "INCLUDES", "HTTP", "USES", "DECLARES", "REFERENCES", "DEPLOYS", "CONNECTS", "USES_QUEUE", "CONFIGURED"]);

interface P { x: number; y: number; vx: number; vy: number }
const W = 900, H = 600;

export interface Group { id: string; name: string; ids: string[] }
/** The repositories (or document collections) the nodes belong to, biggest first. */
export function repoGroups(nodes: GraphNode[]): Group[] {
  const m = new Map<string, Group>();
  for (const n of nodes) {
    const g = m.get(n.repo_id) ?? { id: n.repo_id, name: n.repo, ids: [] };
    g.ids.push(n.id);
    m.set(n.repo_id, g);
  }
  return [...m.values()].sort((a, b) => b.ids.length - a.ids.length);
}

const worldRadius = (count: number) => 70 + 15 * Math.sqrt(count);

/** Where each repository's "world" sits: spread on a ring, then repositories that are linked are drawn together and the rest pushed apart. */
function placeWorlds(groups: Group[], edges: GraphEdge[], repoOf: Record<string, string>): Record<string, { x: number; y: number; r: number }> {
  const n = groups.length;
  const r = groups.map((g) => worldRadius(g.ids.length));
  const ring = Math.max(300, r.reduce((a, b) => a + b, 0) * 0.95); // start well apart, so only a real link pulls two worlds together
  const c = groups.map((_, i) => ({ x: W / 2 + Math.cos((i / n) * Math.PI * 2) * ring, y: H / 2 + Math.sin((i / n) * Math.PI * 2) * ring * 0.7 }));
  const idx = new Map(groups.map((g, i) => [g.id, i]));
  const links = new Map<string, number>();
  for (const e of edges) {
    const a = idx.get(repoOf[e.source]), b = idx.get(repoOf[e.target]);
    if (a == null || b == null || a === b) continue;
    const key = a < b ? `${a}|${b}` : `${b}|${a}`;
    links.set(key, (links.get(key) ?? 0) + 1);
  }
  for (let it = 0; it < 180; it++) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = c[j].x - c[i].x, dy = c[j].y - c[i].y;
        const d = Math.hypot(dx, dy) || 1;
        dx /= d; dy /= d;
        const apart = r[i] + r[j] + 90, k = links.get(`${i}|${j}`) ?? 0;
        let move = 0;
        if (d < apart) move = -(apart - d) * 0.08; // never overlap
        else if (k) move = Math.min((d - (r[i] + r[j] + 40)) * 0.03 * Math.min(k, 6), 40); // linked worlds sit closer
        c[i].x += dx * move / 2; c[i].y += dy * move / 2; c[j].x -= dx * move / 2; c[j].y -= dy * move / 2;
      }
    }
    for (const p of c) { p.x += (W / 2 - p.x) * 0.002; p.y += (H / 2 - p.y) * 0.002; }
  }
  return Object.fromEntries(groups.map((g, i) => [g.id, { x: c[i].x, y: c[i].y, r: r[i] }]));
}

/** Small dependency-free force layout (repulsion + springs + gravity), run to rest then rendered statically.
 *  With more than one repository, each gets its own region ("world") and only links between repositories reach across. */
export function layout(nodes: GraphNode[], edges: GraphEdge[], seedPositions: Record<string, P> = {}): Record<string, P> {
  const pos: Record<string, P> = {};
  const groups = repoGroups(nodes);
  const repoOf: Record<string, string> = {};
  nodes.forEach((n) => { repoOf[n.id] = n.repo_id; });
  const worlds = groups.length > 1 ? placeWorlds(groups, edges, repoOf) : null;
  nodes.forEach((n, i) => {
    const a = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
    const w = worlds?.[n.repo_id];
    const start = w ? { x: w.x + Math.cos(a * 7) * w.r * 0.6, y: w.y + Math.sin(a * 7) * w.r * 0.6 } : { x: W / 2 + Math.cos(a) * 220 + (i % 7), y: H / 2 + Math.sin(a) * 160 + (i % 5) };
    pos[n.id] = seedPositions[n.id] ?? { ...start, vx: 0, vy: 0 };
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
      const across = worlds != null && repoOf[e.source] !== repoOf[e.target]; // a link out of one world into another
      const rest = across ? 200 : e.rel === "CONTAINS" ? 50 : 90;
      const f = (d - rest) * 0.02 * (0.5 + e.weight / 2) * (across ? 0.4 : 1);
      a.vx += (dx / d) * f; a.vy += (dy / d) * f; b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    for (const n of nodes) {
      const p = pos[n.id];
      const home = worlds?.[n.repo_id]; // pulled towards its own world, not the middle of everything
      p.vx += ((home?.x ?? W / 2) - p.x) * (home ? 0.014 : 0.004); p.vy += ((home?.y ?? H / 2) - p.y) * (home ? 0.014 : 0.004);
      p.x += Math.max(-20, Math.min(20, p.vx)) * cool; p.y += Math.max(-20, Math.min(20, p.vy)) * cool;
      p.vx *= 0.6; p.vy *= 0.6;
    }
  }
  return pos;
}

export function fitView(nodes: GraphNode[], pos: Record<string, P>, pad = 0): { x: number; y: number; k: number } {
  const pts = nodes.map((n) => pos[n.id]).filter(Boolean);
  if (!pts.length) return { x: 0, y: 0, k: 1 };
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad, minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
  const w = Math.max(maxX - minX, 80), h = Math.max(maxY - minY, 80);
  const k = Math.max(0.4, Math.min((W * 0.88) / w, (H * 0.86) / h, 2.2));
  return { k, x: W / 2 - ((minX + maxX) / 2) * k, y: H / 2 - ((minY + maxY) / 2) * k };
}

interface Props {
  nodes: GraphNode[]; edges: GraphEdge[]; selectedId: string | null; highlight?: Set<string>;
  onSelect: (id: string) => void; heat?: Record<string, number>; byCommunity?: boolean;
  /** dim everything outside this area (community number) */
  focusArea?: number | null;
  /** move the view to this node; a new `n` re-centres even on the same node */
  centerOn?: { id: string; n: number } | null;
  /** take all the height the parent gives (full-screen mode) instead of a fixed height */
  fill?: boolean;
}

const MIN_K = 0.2, MAX_K = 6;
const clampK = (k: number) => Math.max(MIN_K, Math.min(MAX_K, k));
type LabelMode = "smart" | "all" | "none";

export default function ForceGraph({ nodes, edges, selectedId, highlight, onSelect, heat, byCommunity, focusArea, centerOn, fill }: Props) {
  const posRef = useRef<Record<string, P>>({});
  const pos = useMemo(() => {
    posRef.current = layout(nodes, edges, posRef.current);
    return posRef.current;
  }, [nodes, edges]);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const [labelMode, setLabelMode] = useState<LabelMode>("smart");
  const [tall, setTall] = useState(false);
  const [animate, setAnimate] = useState(true);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const svg = useRef<SVGSVGElement>(null);

  // Pointer position in the drawing's own units (the SVG is scaled to its box); falls back to the centre when unmeasured.
  const toBox = (clientX: number, clientY: number) => {
    const r = svg.current?.getBoundingClientRect();
    if (!r || !r.width || !r.height) return { x: W / 2, y: H / 2, sx: 1, sy: 1 };
    return { x: (clientX - r.left) * (W / r.width), y: (clientY - r.top) * (H / r.height), sx: W / r.width, sy: H / r.height };
  };
  /** Zoom keeping the point (px, py) still, so the thing under the pointer stays under it. */
  const zoomAt = (factor: number, px = W / 2, py = H / 2) =>
    setView((v) => { const k = clampK(v.k * factor), f = k / v.k; return { k, x: px - (px - v.x) * f, y: py - (py - v.y) * f }; });

  // Fit the layout to the canvas whenever the node set changes (pan/zoom afterwards is the user's).
  const fitKey = nodes.map((n) => n.id).join("|");
  const pad = repoGroups(nodes).length > 1 ? 60 : 0; // room for the repository names above each world
  useEffect(() => setView(fitView(nodes, pos, pad)), [fitKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const p = centerOn ? pos[centerOn.id] : undefined;
    if (p) setView((v) => { const k = Math.max(v.k, 1.4); return { k, x: W / 2 - p.x * k, y: H / 2 - p.y * k }; });
  }, [centerOn?.id, centerOn?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  const focus = hover ?? selectedId;
  const calm = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const moving = animate && !calm;
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
  /** One region per repository when there is more than one: where its nodes ended up, so the name can sit above them. */
  const worlds = useMemo(() => {
    const groups = repoGroups(nodes);
    if (groups.length < 2) return [];
    return groups.flatMap((g, i) => {
      const pts = g.ids.map((id) => pos[id]).filter(Boolean);
      if (!pts.length) return [];
      const cx = pts.reduce((a, p) => a + p.x, 0) / pts.length, cy = pts.reduce((a, p) => a + p.y, 0) / pts.length;
      const r = Math.max(...pts.map((p) => Math.hypot(p.x - cx, p.y - cy))) + 34;
      return [{ ...g, cx, cy, r, color: COMMUNITY_COLORS[i % COMMUNITY_COLORS.length] }];
    });
  }, [nodes, pos]);
  const repoOfNode = useMemo(() => new Map(nodes.map((n) => [n.id, n.repo_id])), [nodes]);
  const near = (id: string) => !focus || id === focus || adj.get(focus)?.has(id);
  const inArea = (id: string) => focusArea == null || nodeById.get(id)?.community === focusArea;

  /** The links of the focused node (or of a highlighted path), each as a segment that starts at the node it flows out of. */
  const flowing = useMemo(() => {
    const out: { e: GraphEdge; x1: number; y1: number; x2: number; y2: number }[] = [];
    for (const e of edges) {
      const fromFocus = !!focus && (e.source === focus || e.target === focus);
      const onPath = !!highlight?.has(e.id);
      if (!fromFocus && !onPath) continue;
      const startId = fromFocus ? focus! : e.source, endId = startId === e.source ? e.target : e.source;
      const a = pos[startId], b = pos[endId], na = nodeById.get(startId), nb = nodeById.get(endId);
      if (!a || !b || !na || !nb || !inArea(startId) || !inArea(endId)) continue;
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
      if (len < 1) continue;
      const ra = radius(na) + 1, rb = radius(nb) + 1;
      out.push({ e, x1: a.x + (dx / len) * ra, y1: a.y + (dy / len) * ra, x2: b.x - (dx / len) * rb, y2: b.y - (dy / len) * rb });
      if (out.length >= 60) break;
    }
    return out;
  }, [edges, focus, highlight, pos, nodeById, focusArea]); // eslint-disable-line react-hooks/exhaustive-deps

  const exportSvg = () => {
    const el = svg.current;
    if (!el) return;
    const copy = el.cloneNode(true) as SVGSVGElement;
    copy.querySelectorAll("[data-flow]").forEach((x) => x.remove()); // motion belongs on screen, not in the file
    copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    copy.setAttribute("width", String(W)); copy.setAttribute("height", String(H));
    copy.setAttribute("style", "background:#fff;--color-ink:#0f172a;--color-surface:#ffffff");  // the page's colour variables do not exist in the file
    try {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)], { type: "image/svg+xml" }));
      a.download = "repolens-graph.svg"; a.click(); URL.revokeObjectURL(a.href);
    } catch { /* the browser cannot save files here */ }
  };
  const btn = "grid h-8 w-8 place-items-center rounded-md text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2";

  return (
    <div className={fill ? "relative min-h-0 flex-1" : "relative"}>
      <div role="toolbar" aria-label="Graph controls" className="absolute right-2 top-2 z-10 flex items-center gap-0.5 rounded-lg border border-line bg-white/95 p-1 shadow-sm">
        <button className={btn} aria-label="Zoom in" title="Zoom in (+)" onClick={() => zoomAt(1.3)}><Plus className="h-4 w-4" /></button>
        <button className={btn} aria-label="Zoom out" title="Zoom out (-)" onClick={() => zoomAt(1 / 1.3)}><Minus className="h-4 w-4" /></button>
        <span className="w-10 text-center text-[11px] tabular-nums text-muted" aria-label="Zoom level">{Math.round(view.k * 100)}%</span>
        <button className={btn} aria-label="Fit to screen" title="Fit everything (0)" onClick={() => setView(fitView(nodes, pos, pad))}><ScanSearch className="h-4 w-4" /></button>
        <button className={btn} aria-label="Actual size" title="Reset to 100%" onClick={() => setView({ x: 0, y: 0, k: 1 })}><Expand className="h-4 w-4" /></button>
        <button className={btn} aria-label="Animate connections" aria-pressed={animate} title={calm ? "Your system asks for less motion, so the animation stays off" : "Show flow from the selected node to what it connects to"} onClick={() => setAnimate((a) => !a)}>{animate && !calm ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}</button>
        <select aria-label="Labels" title="Which labels to draw" className="h-8 rounded-md border-0 bg-transparent px-1 text-xs text-slate-600" value={labelMode} onChange={(e) => setLabelMode(e.target.value as LabelMode)}>
          <option value="smart">Key labels</option><option value="all">All labels</option><option value="none">No labels</option></select>
        <button className={btn} aria-label="Download SVG" title="Download as SVG" onClick={exportSvg}><Download className="h-4 w-4" /></button>
        {!fill && <button className={btn} aria-label={tall ? "Shrink graph" : "Enlarge graph"} aria-pressed={tall} title={tall ? "Shrink" : "Enlarge"} onClick={() => setTall((t) => !t)}>{tall ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}</button>}
      </div>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Knowledge graph" tabIndex={0}
        className={`${fill ? "h-full" : tall ? "h-[46rem]" : "h-[34rem]"} w-full cursor-grab rounded-lg border border-line bg-white select-none`}
        onWheel={(e) => { e.preventDefault(); const b = toBox(e.clientX, e.clientY); zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, b.x, b.y); }}
        onDoubleClick={(e) => { const b = toBox(e.clientX, e.clientY); zoomAt(1.6, b.x, b.y); }}
        onKeyDown={(e) => {
          const step = 40;
          if (e.key === "+" || e.key === "=") zoomAt(1.3);
          else if (e.key === "-" || e.key === "_") zoomAt(1 / 1.3);
          else if (e.key === "0") setView(fitView(nodes, pos, pad));
          else if (e.key === "ArrowLeft") setView((v) => ({ ...v, x: v.x + step }));
          else if (e.key === "ArrowRight") setView((v) => ({ ...v, x: v.x - step }));
          else if (e.key === "ArrowUp") setView((v) => ({ ...v, y: v.y + step }));
          else if (e.key === "ArrowDown") setView((v) => ({ ...v, y: v.y - step }));
          else return;
          e.preventDefault();
        }}
        onMouseDown={(e) => { drag.current = { x: e.clientX, y: e.clientY }; }}
        onMouseMove={(e) => {
          if (!drag.current) return;
          const b = toBox(e.clientX, e.clientY), dx = (e.clientX - drag.current.x) * b.sx, dy = (e.clientY - drag.current.y) * b.sy;
          drag.current = { x: e.clientX, y: e.clientY };
          setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
        }}
        onMouseUp={() => { drag.current = null; }} onMouseLeave={() => { drag.current = null; }}>
        <defs>
          {Object.entries(REL_COLORS).map(([rel, c]) => (
            <marker key={rel} id={`arrow-${rel}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={c} /></marker>))}
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {worlds.map((w) => (
            <g key={w.id} data-world={w.name} pointerEvents="none">
              <circle cx={w.cx} cy={w.cy} r={w.r} fill={w.color} fillOpacity={0.05} stroke={w.color} strokeOpacity={0.4} strokeDasharray="6 5" />
              <text x={w.cx} y={w.cy - w.r - 8} textAnchor="middle" fontSize={15} fontWeight={600} fill={w.color} style={{ stroke: "var(--color-surface)" }} strokeWidth={3} paintOrder="stroke">{w.name} · {w.ids.length} nodes</text>
            </g>))}
          {edges.map((e) => {
            const a = pos[e.source], b = pos[e.target];
            if (!a || !b) return null;
            const on = highlight?.has(e.id) || (focus && (e.source === focus || e.target === focus));
            const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
            const tn = nodeById.get(e.target);
            const end = DIRECTED.has(e.rel) && tn ? radius(tn) + 2 : 0; // stop at the node edge so the arrowhead is visible
            const away = !inArea(e.source) || !inArea(e.target);
            const bridge = worlds.length > 0 && repoOfNode.get(e.source) !== repoOfNode.get(e.target); // from one repository's world into another's
            return (
              <g key={e.id} data-bridge={bridge ? "" : undefined}>
                <line x1={a.x} y1={a.y} x2={b.x - (dx / len) * end} y2={b.y - (dy / len) * end} stroke={bridge ? "#d97706" : (REL_COLORS[e.rel] ?? "#94a3b8")}
                  markerEnd={DIRECTED.has(e.rel) && (on || !focus) ? `url(#arrow-${e.rel})` : undefined}
                  strokeWidth={on || bridge ? 2.5 : 1} strokeDasharray={bridge ? "7 4" : trustDash(e.trust ?? (e.provenance === "semantic" ? "inferred" : "fact"))}
                  opacity={away ? 0.06 : on ? 0.95 : focus ? (bridge ? 0.5 : 0.12) : bridge ? 0.9 : e.rel === "CONTAINS" ? 0.35 : 0.55}><title>{`${bridge ? "BRIDGE between repositories: " : ""}${e.rel} (${e.trust ?? e.provenance}: ${e.provenance}, ${e.weight})\n${e.evidence}`}</title></line>
                {on && !away && e.rel !== "CONTAINS" && <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 3} textAnchor="middle" fontSize={9} fill={REL_COLORS[e.rel] ?? "#475569"} style={{ stroke: "var(--color-surface)" }} strokeWidth={3} paintOrder="stroke">{e.rel.toLowerCase().replace("_", " ")}</text>}
              </g>);
          })}
          {moving && flowing.map(({ e, x1, y1, x2, y2 }) => {
            const d = `M${x1} ${y1} L${x2} ${y2}`, c = REL_COLORS[e.rel] ?? "#4f46e5";
            return (
              <g key={`flow-${e.id}`} data-flow="" pointerEvents="none">
                <path d={d} fill="none" stroke={c} strokeWidth={2.2} strokeLinecap="round" strokeDasharray="6 8" className="rl-flow" opacity={0.9} />
                <circle r={3.2} fill={c}><animateMotion dur="1.6s" repeatCount="indefinite" path={d} /></circle>
              </g>);
          })}
          {moving && selectedId && pos[selectedId] && nodeById.get(selectedId) && (
            <circle data-flow="" pointerEvents="none" cx={pos[selectedId].x} cy={pos[selectedId].y} fill="none" stroke="#6366f1" strokeWidth={2}>
              <animate attributeName="r" values={`${radius(nodeById.get(selectedId)!) + 2};${radius(nodeById.get(selectedId)!) + 16}`} dur="1.8s" repeatCount="indefinite" />
              <animate attributeName="opacity" values="0.7;0" dur="1.8s" repeatCount="indefinite" />
            </circle>)}
          {nodes.map((n) => {
            const p = pos[n.id];
            if (!p) return null;
            const sel = n.id === selectedId;
            const showLabel = labelMode === "none" ? sel || n.id === hover : labelMode === "all" || sel || n.id === hover || n.degree >= maxDeg * 0.35 || nodes.length < 25 || (focus && adj.get(focus)?.has(n.id));
            return (
              <g key={n.id} transform={`translate(${p.x} ${p.y})`} opacity={inArea(n.id) ? (near(n.id) ? 1 : 0.25) : 0.12} className="cursor-pointer"
                onClick={(ev) => { ev.stopPropagation(); onSelect(n.id); }} onDoubleClick={(ev) => ev.stopPropagation()} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
                onMouseDown={(ev) => ev.stopPropagation()} role="button" aria-label={`${n.type} ${n.qualname || n.name}`}>
                <circle r={radius(n)} fill={heat ? (n.id in heat ? heatColor(heat[n.id]) : "#e2e8f0") : byCommunity ? communityColor(n.community) : (NODE_COLORS[n.type] ?? "#94a3b8")} style={{ stroke: sel ? "var(--color-ink)" : highlight?.has(n.id) ? "#ec4899" : "var(--color-surface)" }} strokeWidth={sel || highlight?.has(n.id) ? 3 : 1.5} />
                <title>{`${n.type} ${n.qualname || n.name}\n${n.degree} connections${n.community != null ? `\narea ${n.community + 1}` : ""}${heat && n.id in heat ? `\nheat ${Math.round(heat[n.id] * 100)}%` : ""}`}</title>
                {showLabel && <text y={-radius(n) - 4} textAnchor="middle" fontSize={10} style={{ fill: "var(--color-ink)", stroke: "var(--color-surface)" }} strokeWidth={3} paintOrder="stroke">{n.name.length > 28 ? `${n.name.slice(0, 27)}…` : n.name}</text>}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
