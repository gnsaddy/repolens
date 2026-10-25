import { useEffect, useState } from "react";

export type ThemePref = "system" | "light" | "dark";
const KEY = "repolens.theme";

export function storedTheme(): ThemePref {
  try { const v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : "system"; } catch { return "system"; }
}

const prefersDark = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;

/** Put the page in night mode (or not) for a preference; "system" follows the device. */
export function applyTheme(pref: ThemePref): boolean {
  const dark = pref === "dark" || (pref === "system" && prefersDark());
  document.documentElement.classList.toggle("dark", dark);
  return dark;
}

export function setTheme(pref: ThemePref): void {
  try { if (pref === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, pref); } catch { /* storage unavailable: still applies for this visit */ }
  applyTheme(pref);
  window.dispatchEvent(new Event("repolens-theme"));
}

/** Apply the saved preference now and follow the device setting while the preference is "system". */
export function initTheme(): void {
  applyTheme(storedTheme());
  if (typeof window.matchMedia === "function") window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => { if (storedTheme() === "system") applyTheme("system"); });
}

export function useTheme(): { pref: ThemePref; dark: boolean; set: (p: ThemePref) => void; toggle: () => void } {
  const [pref, setPref] = useState<ThemePref>(storedTheme);
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const sync = () => { setPref(storedTheme()); setDark(document.documentElement.classList.contains("dark")); };
    window.addEventListener("repolens-theme", sync);
    return () => window.removeEventListener("repolens-theme", sync);
  }, []);
  return { pref, dark, set: setTheme, toggle: () => setTheme(dark ? "light" : "dark") };
}
