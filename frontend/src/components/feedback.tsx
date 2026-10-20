import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui";

type Tone = "success" | "error" | "info";
interface ConfirmOptions { title: string; message?: ReactNode; confirmLabel?: string; danger?: boolean }
interface Ctx { toast: (text: string, tone?: Tone) => void; confirm: (o: ConfirmOptions) => Promise<boolean> }

// Without a provider (unit tests, storybook) fall back to the browser's own dialogs so callers never need a guard.
const Fallback: Ctx = { toast: () => {}, confirm: (o) => Promise.resolve(window.confirm(o.title)) };
const FeedbackContext = createContext<Ctx>(Fallback);
export const useToast = () => useContext(FeedbackContext).toast;
export const useConfirm = () => useContext(FeedbackContext).confirm;

const ICON = { success: CheckCircle2, error: AlertTriangle, info: Info };
const TONE = { success: "border-emerald-200 bg-emerald-50 text-emerald-800", error: "border-red-200 bg-red-50 text-red-800", info: "border-indigo-200 bg-indigo-50 text-indigo-800" };

/** Toast notifications and an accessible confirmation dialog, replacing window.confirm. */
export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; text: string; tone: Tone }[]>([]);
  const [dialog, setDialog] = useState<(ConfirmOptions & { resolve: (v: boolean) => void }) | null>(null);
  const next = useRef(1);
  const toast = useCallback((text: string, tone: Tone = "success") => {
    const id = next.current++;
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 8000 : 4000);
  }, []);
  const confirm = useCallback((o: ConfirmOptions) => new Promise<boolean>((resolve) => setDialog({ ...o, resolve })), []);
  const close = (v: boolean) => { dialog?.resolve(v); setDialog(null); };
  const value = useMemo(() => ({ toast, confirm }), [toast, confirm]);

  useEffect(() => {
    if (!dialog) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { dialog.resolve(false); setDialog(null); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dialog]);

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" aria-live="polite">
        {toasts.map((t) => {
          const Icon = ICON[t.tone];
          return (
            <div key={t.id} role={t.tone === "error" ? "alert" : "status"} className={`pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-sm shadow-lg ${TONE[t.tone]}`}>
              <Icon className="mt-0.5 h-4 w-4 shrink-0" /><span className="flex-1">{t.text}</span>
              <button aria-label="Dismiss" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}><X className="h-3.5 w-3.5" /></button>
            </div>);
        })}
      </div>
      {dialog && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4" onMouseDown={() => close(false)}>
          <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl" onMouseDown={(e) => e.stopPropagation()}>
            <h2 id="confirm-title" className="text-base font-semibold">{dialog.title}</h2>
            {dialog.message && <div className="mt-2 text-sm text-muted">{dialog.message}</div>}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" onClick={() => close(false)}>Cancel</Button>
              <Button variant={dialog.danger ? "danger" : "primary"} autoFocus onClick={() => close(true)}>{dialog.confirmLabel ?? "Confirm"}</Button>
            </div>
          </div>
        </div>)}
    </FeedbackContext.Provider>
  );
}
