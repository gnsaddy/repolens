import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { Activity } from "lucide-react";
import { Button, Card, ErrorNote, Input, Label } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function Login() {
  const { user, login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [f, setF] = useState({ email: "", password: "", org: "", name: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      if (mode === "login") await login(f.email, f.password);
      else await register(f.email, f.password, f.org || "My Organization", f.name);
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  return (
    <div className="grid min-h-screen place-items-center bg-sidebar p-4">
      <Card className="w-full max-w-sm">
        <div className="mb-5 flex items-center gap-2"><Activity className="h-5 w-5 text-brand" /><span className="font-semibold">RepoLens AI</span></div>
        <h1 className="mb-4 text-lg font-semibold">{mode === "login" ? "Sign in" : "Create your workspace"}</h1>
        <form onSubmit={submit} className="space-y-3">
          {mode === "register" && (<>
            <label className="block"><Label>Organization</Label><Input value={f.org} onChange={(e) => setF({ ...f, org: e.target.value })} placeholder="Acme Engineering" /></label>
            <label className="block"><Label>Your name</Label><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          </>)}
          <label className="block"><Label>Email</Label><Input type="email" required autoComplete="username" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label className="block"><Label>Password</Label><Input type="password" required minLength={8} autoComplete={mode === "login" ? "current-password" : "new-password"} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
          <ErrorNote error={err} />
          <Button className="w-full" disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</Button>
        </form>
        <button className="mt-4 text-xs text-brand hover:underline" onClick={() => { setMode(mode === "login" ? "register" : "login"); setErr(null); }}>
          {mode === "login" ? "New here? Create a workspace" : "Have an account? Sign in"}
        </button>
      </Card>
    </div>
  );
}
