import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** Try the credentials typed into a form (nothing is saved) and report who they belong to. */
export function useCheck(path: string, body: () => unknown) {
  const m = useMutation({ mutationFn: () => api<{ ok: boolean; user?: string; error?: string }>(path, { method: "POST", json: body() }) });
  return {
    run: () => m.mutate(),
    pending: m.isPending,
    reset: m.reset,
    message: m.error ? <p role="status" className="text-sm text-red-600">{(m.error as Error).message}</p>
      : m.data ? <p role="status" className={`text-sm ${m.data.ok ? "text-emerald-700" : "text-red-600"}`}>{m.data.ok ? `Works — signed in as ${m.data.user}` : m.data.error}</p> : null,
  };
}
