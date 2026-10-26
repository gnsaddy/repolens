import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

/** Day or night mode for pages people see before signing in. The choice is remembered and shared with the rest of the app. */
export default function ThemeToggle({ className }: { className?: string }) {
  const theme = useTheme();
  return (
    <button type="button" onClick={theme.toggle} aria-label={theme.dark ? "Switch to day mode" : "Switch to night mode"} aria-pressed={theme.dark}
      title={theme.dark ? "Day mode" : "Night mode"} className={cn("grid h-9 w-9 place-items-center rounded-md text-slate-300 hover:bg-white/10 focus-visible:outline focus-visible:outline-2", className)}>
      {theme.dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}
