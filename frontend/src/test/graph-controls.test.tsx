import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ForceGraph from "@/components/ForceGraph";
import type { GraphEdge, GraphNode } from "@/lib/api";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const node = (id: string, community: number | null, degree = 2): GraphNode => ({ id, type: "function", name: `fn_${id}`, qualname: `fn_${id}`, path: "a.py", start_line: 1, end_line: 3, repo_id: "r", repo: "svc", degree, meta: {}, community });
const edge = (id: string, s: string, t: string): GraphEdge => ({ id, source: s, target: t, rel: "CALLS", provenance: "static_analysis", trust: "fact", weight: 1, evidence: "" });
const nodes = [node("a", 0), node("b", 0), node("c", 1)];
const edges = [edge("e1", "a", "b"), edge("e2", "b", "c")];
const zoom = () => Number(screen.getByLabelText("Zoom level").textContent!.replace("%", ""));
const drawing = (c: HTMLElement) => c.querySelector("svg[aria-label='Knowledge graph']") as SVGSVGElement;

describe("graph controls", () => {
  it("zooms with the buttons and the keyboard, and fit and actual size bring it back", async () => {
    const { container } = render(<ForceGraph nodes={nodes} edges={edges} selectedId={null} onSelect={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Actual size" }));
    expect(zoom()).toBe(100);
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(zoom()).toBeGreaterThan(100);
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(zoom()).toBeLessThan(100);
    drawing(container).focus();
    await userEvent.keyboard("+");
    const afterKey = zoom();
    await userEvent.keyboard("0"); // fit everything
    expect(zoom()).not.toBe(afterKey);
    for (let i = 0; i < 30; i++) await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(zoom()).toBeLessThanOrEqual(600); // never zooms away into nothing
  });

  it("changes which labels are drawn, enlarges the graph and downloads it as SVG", async () => {
    const many = Array.from({ length: 40 }, (_, i) => node(`n${i}`, 0, i === 0 ? 30 : 1));
    const { container } = render(<ForceGraph nodes={many} edges={[]} selectedId={null} onSelect={() => {}} />);
    const labels = () => drawing(container).querySelectorAll("text").length;
    const key = labels();
    await userEvent.selectOptions(screen.getByLabelText("Labels"), "all");
    expect(labels()).toBe(40);
    await userEvent.selectOptions(screen.getByLabelText("Labels"), "none");
    expect(labels()).toBe(0);
    expect(key).toBeLessThan(40);
    const big = screen.getByRole("button", { name: "Enlarge graph" });
    expect(drawing(container).getAttribute("class")).toContain("h-[34rem]");
    await userEvent.click(big);
    expect(drawing(container).getAttribute("class")).toContain("h-[46rem]");
    expect(screen.getByRole("button", { name: "Shrink graph" })).toHaveAttribute("aria-pressed", "true");

    const saved: Blob[] = [];
    (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = (b) => { saved.push(b); return "blob:x"; };
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};
    await userEvent.click(screen.getByRole("button", { name: "Download SVG" }));
    expect(saved).toHaveLength(1);
    const text = await saved[0].text();
    expect(text).toContain("xmlns=\"http://www.w3.org/2000/svg\"");
    expect(text).toContain("--color-ink"); // the page's colour variables travel with the file
  });

  it("moves the view to a node it is asked to centre on, and dims everything outside a chosen area", async () => {
    const { container, rerender } = render(<ForceGraph nodes={nodes} edges={edges} selectedId={null} onSelect={() => {}} />);
    const transform = () => container.querySelector("svg[aria-label='Knowledge graph'] > g")!.getAttribute("transform");
    const before = transform();
    rerender(<ForceGraph nodes={nodes} edges={edges} selectedId={null} onSelect={() => {}} centerOn={{ id: "c", n: 1 }} />);
    expect(transform()).not.toBe(before);
    rerender(<ForceGraph nodes={nodes} edges={edges} selectedId={null} onSelect={() => {}} byCommunity focusArea={1} />);
    const opacity = (name: string) => within(drawing(container) as unknown as HTMLElement).getByRole("button", { name }).getAttribute("opacity");
    expect(opacity("function fn_c")).toBe("1"); // in the area
    expect(Number(opacity("function fn_a"))).toBeLessThan(0.2); // outside it
  });

  describe("flow animation", () => {
    const flow = (c: HTMLElement) => drawing(c).querySelectorAll("path.rl-flow");
    const noMotionPreference = () => vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));

    it("flows from the selected node to every node it connects to, and pulses the selected node", () => {
      noMotionPreference();
      const { container } = render(<ForceGraph nodes={nodes} edges={edges} selectedId="b" onSelect={() => {}} />);
      expect(flow(container)).toHaveLength(2); // b connects to a and to c
      expect(drawing(container).querySelectorAll("[data-flow] animateMotion")).toHaveLength(2); // one particle per link
      expect(drawing(container).querySelectorAll("circle[data-flow] animate")).toHaveLength(2); // the pulse ring
      // every flowing segment starts at b, so the motion reads as outward from it
      const b = drawing(container).querySelector("g[aria-label='function fn_b']")!.getAttribute("transform")!;
      const [bx, by] = b.match(/-?\d+(\.\d+)?/g)!.map(Number);
      for (const path of flow(container)) {
        const [x, y] = path.getAttribute("d")!.match(/-?\d+(\.\d+)?/g)!.slice(0, 2).map(Number);
        expect(Math.hypot(x - bx, y - by)).toBeLessThan(20); // starts at the edge of the node, not somewhere else
      }
    });

    it("follows the node under the pointer", async () => {
      noMotionPreference();
      const { container } = render(<ForceGraph nodes={nodes} edges={edges} selectedId={null} onSelect={() => {}} />);
      expect(flow(container)).toHaveLength(0); // nothing is focused, nothing moves
      await userEvent.hover(within(drawing(container) as unknown as HTMLElement).getByRole("button", { name: "function fn_a" }));
      expect(flow(container)).toHaveLength(1); // a has one link
      await userEvent.unhover(within(drawing(container) as unknown as HTMLElement).getByRole("button", { name: "function fn_a" }));
      expect(flow(container)).toHaveLength(0);
    });

    it("can be paused, stays off when the system asks for less motion, and is not saved into the SVG file", async () => {
      noMotionPreference();
      const { container } = render(<ForceGraph nodes={nodes} edges={edges} selectedId="b" onSelect={() => {}} />);
      const toggle = screen.getByRole("button", { name: "Animate connections" });
      expect(toggle).toHaveAttribute("aria-pressed", "true");

      const saved: Blob[] = [];
      (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = (b) => { saved.push(b); return "blob:x"; };
      (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};
      await userEvent.click(screen.getByRole("button", { name: "Download SVG" }));
      const file = await saved[0].text();
      expect(file).not.toContain("rl-flow");
      expect(file).not.toContain("animateMotion");
      expect(flow(container)).toHaveLength(2); // the file is a copy; the screen keeps moving

      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-pressed", "false");
      expect(flow(container)).toHaveLength(0);
      expect(drawing(container).querySelector("[data-flow]")).toBeNull();
    });

    it("does not move at all for people who asked their system for less motion", () => {
      vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {} }));
      const { container } = render(<ForceGraph nodes={nodes} edges={edges} selectedId="b" onSelect={() => {}} />);
      expect(flow(container)).toHaveLength(0);
      expect(drawing(container).querySelector("[data-flow]")).toBeNull();
    });
  });
});
