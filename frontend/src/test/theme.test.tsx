import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, initTheme, setTheme, storedTheme, useTheme } from "@/lib/theme";

function mockSystem(dark: boolean) {
  const listeners: (() => void)[] = [];
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: dark, addEventListener: (_: string, fn: () => void) => listeners.push(fn) })));
  return listeners;
}
beforeEach(() => { localStorage.clear(); document.documentElement.classList.remove("dark"); });
afterEach(() => vi.unstubAllGlobals());

describe("night mode", () => {
  it("follows the device until a choice is made, and a choice is remembered", () => {
    mockSystem(true);
    expect(storedTheme()).toBe("system");
    expect(applyTheme("system")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    setTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem("repolens.theme")).toBe("light");
    expect(storedTheme()).toBe("light");
    setTheme("system");
    expect(localStorage.getItem("repolens.theme")).toBeNull();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
  it("applies a saved night choice at start and reacts to the device changing only while on 'system'", () => {
    const listeners = mockSystem(false);
    localStorage.setItem("repolens.theme", "dark");
    initTheme();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: () => undefined })));
    listeners.forEach((fn) => fn());
    expect(document.documentElement.classList.contains("dark")).toBe(true); // an explicit choice wins over the device
    localStorage.removeItem("repolens.theme");
    listeners.forEach((fn) => fn());
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
  it("survives a browser without storage", () => {
    mockSystem(false);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(() => setTheme("dark")).not.toThrow();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
  it("the toggle flips between day and night and the label says what it will do", async () => {
    mockSystem(false);
    function Toggle() { const t = useTheme(); return <button onClick={t.toggle}>{t.dark ? "Day mode" : "Night mode"}</button>; }
    render(<Toggle />);
    await userEvent.click(screen.getByRole("button", { name: "Night mode" }));
    expect(await screen.findByRole("button", { name: "Day mode" })).toBeInTheDocument();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
