import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import Logo from "@/components/Logo";
import { GithubLogo } from "@phosphor-icons/react";
import Landing from "@/components/Landing";
import { Card, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

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
  const [github, setGithub] = useState(false);  // the server has a GitHub OAuth app and a workspace allows it
  useEffect(() => { api<{ enabled: boolean }>("/auth/github/enabled").then((r) => setGithub(!!r.enabled)).catch(() => setGithub(false)); }, []);
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
  const field = "body-5 mt-1 block w-full rounded-lg border border-l-line bg-l-bg px-3 py-2 text-l-text placeholder:text-l-muted";
  const lab = "caption-1 text-l-text";
  const btn = "button-md inline-flex w-full items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 transition-colors duration-300 ease-land active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50";
  const primary = cn(btn, "bg-brand text-white hover:bg-brand-600");
  const outline = cn(btn, "border border-l-line bg-l-card text-l-text hover:bg-l-sunk");
  const card = (
    <div className="w-full rounded-2xl border border-l-line bg-l-card p-6">
      <div className="mb-6 flex items-center gap-2"><Logo className="h-8 w-8" /><span className="title-2 text-l-text">RepoLens AI</span></div>
      <h2 className="heading-5 mb-6 text-l-text">{mode === "login" ? "Sign in" : "Create your workspace"}</h2>
      <form onSubmit={submit} className="space-y-4">
        {mode === "register" && (<>
          <label className="block"><span className={lab}>Organization</span><input className={field} value={f.org} onChange={(e) => setF({ ...f, org: e.target.value })} placeholder="Acme Engineering" /></label>
          <label className="block"><span className={lab}>Your name</span><input className={field} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        </>)}
        <label className="block"><span className={lab}>Email</span><input className={field} type="email" required autoComplete="username" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
        <label className="block"><span className={lab}>Password</span><input className={field} type="password" required minLength={8} autoComplete={mode === "login" ? "current-password" : "new-password"} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
        {needCode && <label className="block"><span className={lab}>Authentication code</span><input className={field} autoFocus inputMode="numeric" autoComplete="one-time-code" aria-label="Authentication code" placeholder="6 digit code, or a recovery code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /><span className="caption-5 mt-1 block text-l-muted">Open your authenticator app. Lost your phone? Use a recovery code, or ask an admin to reset it.</span></label>}
        <ErrorNote error={err} />
        {ssoError && <p role="alert" className="caption-2 text-red-600">{ssoError}</p>}
        {sso && <button type="button" className={outline} onClick={() => { window.location.href = `/api/auth/sso/start?email=${encodeURIComponent(f.email)}`; }}>Continue with single sign on{sso.organization ? ` (${sso.organization})` : ""}</button>}
        <button className={primary} disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</button>
        {github && mode === "login" && <button type="button" className={outline} onClick={() => { window.location.href = "/api/auth/github/start"; }}><GithubLogo size={20} aria-hidden />Continue with GitHub</button>}
      </form>
      <button className="caption-1 mt-6 text-l-link hover:underline" onClick={() => { setMode(mode === "login" ? "register" : "login"); setErr(null); }}>
        {mode === "login" ? "New here? Create a workspace" : "Have an account? Sign in"}
      </button>
    </div>
  );
  return <Landing card={card} />;
}

/** Landing page after the identity provider: trades the one time code for a session. */
export function SsoComplete() {
  const { completeSso } = useAuth();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    const code = sp.get("code");
    if (!code) { setErr(new Error("Missing sign in code")); return; }
    completeSso(code).then(() => nav("/", { replace: true })).catch(setErr);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="grid min-h-screen place-items-center bg-canvas p-4">
      <Card className="w-full max-w-sm text-center">
        {err ? (<><p role="alert" className="mb-3 body-6 text-red-600">{(err as Error).message}</p><a className="body-6 text-brand hover:underline" href="/login">Back to sign in</a></>) : <p className="body-6 text-muted">Signing you in…</p>}
      </Card>
    </div>
  );
}
