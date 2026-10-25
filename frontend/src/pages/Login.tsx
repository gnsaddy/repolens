import { APP_VERSION } from "@/lib/version";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import Logo from "@/components/Logo";
import { Button, Card, ErrorNote, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function Login() {
  const { user, login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [f, setF] = useState({ email: "", password: "", org: "", name: "", code: "" });
  const [needCode, setNeedCode] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [sp] = useSearchParams();
  const ssoError = sp.get("sso_error");
  const [sso, setSso] = useState<{ organization: string } | null>(null);
  useEffect(() => {
    setSso(null);
    if (mode !== "login" || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email)) return;
    const t = setTimeout(() => {
      api<{ sso: boolean; organization?: string }>(`/auth/sso/discover?email=${encodeURIComponent(f.email)}`)
        .then((r) => setSso(r.sso ? { organization: r.organization ?? "" } : null)).catch(() => setSso(null));
    }, 300);
    return () => clearTimeout(t);
  }, [f.email, mode]);
  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      if (mode === "login") await login(f.email, f.password, f.code);
      else await register(f.email, f.password, f.org || "My Organization", f.name);
    } catch (x) {
      if (x instanceof Error && /Authentication code required/.test(x.message)) { setNeedCode(true); setErr(null); } else setErr(x);
    } finally { setBusy(false); }
  }
  return (
    <div className="grid min-h-screen bg-sidebar lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <aside className="hidden flex-col justify-center gap-6 p-14 text-slate-300 lg:flex" aria-label="About RepoLens AI">
        <div className="flex items-center gap-2 text-white"><Logo className="h-9 w-9" /><span className="text-xl font-semibold">RepoLens AI</span><span className="rounded bg-white/10 px-1.5 py-0.5 text-xs text-slate-300">{APP_VERSION}</span></div>
        <h2 className="max-w-md text-3xl font-semibold leading-tight text-white">Answers from your docs, pull requests and code, with the cost kept low.</h2>
        <ul className="max-w-md space-y-3 text-sm">
          {[["DeepMind Wiki", "One connected knowledge base over READMEs, Confluence and your code."], ["Root-cause investigations", "From a Jira ticket or CI failure to a cited, reviewable report."], ["Fewer tokens", "Cheap models for easy work, shared context and remembered answers."]].map(([t, d]) => (
            <li key={t}><b className="text-white">{t}.</b> {d}</li>))}
        </ul>
      </aside>
      <div className="grid place-items-center p-4">
      <Card className="w-full max-w-sm">
        <div className="mb-5 flex items-center gap-2"><Logo className="h-7 w-7" /><span className="font-semibold">RepoLens AI</span></div>
        <h1 className="mb-4 text-lg font-semibold">{mode === "login" ? "Sign in" : "Create your workspace"}</h1>
        <form onSubmit={submit} className="space-y-3">
          {mode === "register" && (<>
            <label className="block"><Label>Organization</Label><Input value={f.org} onChange={(e) => setF({ ...f, org: e.target.value })} placeholder="Acme Engineering" /></label>
            <label className="block"><Label>Your name</Label><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          </>)}
          <label className="block"><Label>Email</Label><Input type="email" required autoComplete="username" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label className="block"><Label>Password</Label><Input type="password" required minLength={8} autoComplete={mode === "login" ? "current-password" : "new-password"} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
          {needCode && <label className="block"><Label>Authentication code</Label><Input autoFocus inputMode="numeric" autoComplete="one-time-code" aria-label="Authentication code" placeholder="6-digit code, or a recovery code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /><span className="mt-1 block text-xs text-muted">Open your authenticator app. Lost your phone? Use a recovery code, or ask an admin to reset it.</span></label>}
          <ErrorNote error={err} />
          {ssoError && <p role="alert" className="text-sm text-red-600">{ssoError}</p>}
          {sso && <Button type="button" variant="outline" className="w-full" onClick={() => { window.location.href = `/api/auth/sso/start?email=${encodeURIComponent(f.email)}`; }}>Continue with single sign-on{sso.organization ? ` (${sso.organization})` : ""}</Button>}
          <Button className="w-full" disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</Button>
        </form>
        <button className="mt-4 text-xs text-brand hover:underline" onClick={() => { setMode(mode === "login" ? "register" : "login"); setErr(null); }}>
          {mode === "login" ? "New here? Create a workspace" : "Have an account? Sign in"}
        </button>
      </Card>
      </div>
    </div>
  );
}

/** Landing page after the identity provider: trades the one-time code for a session. */
export function SsoComplete() {
  const { completeSso } = useAuth();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    const code = sp.get("code");
    if (!code) { setErr(new Error("Missing sign-in code")); return; }
    completeSso(code).then(() => nav("/", { replace: true })).catch(setErr);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="grid min-h-screen place-items-center bg-sidebar p-4">
      <Card className="w-full max-w-sm text-center">
        {err ? (<><p role="alert" className="mb-3 text-sm text-red-600">{(err as Error).message}</p><a className="text-sm text-brand hover:underline" href="/login">Back to sign in</a></>) : <p className="text-sm text-muted">Signing you in…</p>}
      </Card>
    </div>
  );
}
