import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Laptop, LogOut, Monitor, Moon, ShieldCheck, Smartphone, Sun, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Label, PageHeader } from "@/components/ui";
import { api, type SessionRow, type TotpSetup, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useTheme, type ThemePref } from "@/lib/theme";
import { timeAgo } from "@/lib/utils";

const ROLE_HELP: Record<string, string> = { admin: "Can configure integrations, models and access, and approve changes.", member: "Can add repositories, investigate and propose edits.", viewer: "Can read and search; cannot change anything." };

export default function Profile() {
  const { user, setUser, logout } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const theme = useTheme();
  const [name, setName] = useState(user?.name ?? "");
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const rename = useMutation({ mutationFn: () => api<User>("/auth/me", { method: "PATCH", json: { name } }), onSuccess: (u) => { setUser(u); toast("Profile saved"); } });
  const change = useMutation({
    mutationFn: () => api("/auth/password", { method: "POST", json: { current_password: pw.current, new_password: pw.next } }),
    onSuccess: () => { setPw({ current: "", next: "", again: "" }); toast("Password changed. Other devices were signed out."); qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<SessionRow[]>("/auth/sessions") });
  const end = useMutation({ mutationFn: (id: string) => api(`/auth/sessions/${id}`, { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); toast("Session ended"); } });
  const others = useMutation({ mutationFn: () => api<{ ended: number }>("/auth/logout-others", { method: "POST" }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["sessions"] }); toast(`Signed out of ${r.ended} other ${r.ended === 1 ? "device" : "devices"}`); } });
  if (!user) return null;
  const mismatch = !!pw.again && pw.next !== pw.again;
  const submit = (e: FormEvent) => { e.preventDefault(); if (!mismatch) change.mutate(); };

  return (
    <div>
      <PageHeader title="Your profile" subtitle="Your account, where you are signed in, and how to keep it secure." actions={<Button variant="outline" onClick={logout}><LogOut className="h-4 w-4" />Sign out</Button>} />
      <div className="grid max-w-5xl gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle><UserRound className="mr-1.5 inline h-4 w-4" />Account</CardTitle>
          <div className="mb-4 flex items-center gap-3">
            <div className="grid h-12 w-12 place-items-center rounded-full bg-brand text-lg font-semibold text-white" aria-hidden>{(user.name || user.email)[0].toUpperCase()}</div>
            <div className="min-w-0"><div className="truncate font-medium">{user.name || "No name set"}</div><div className="truncate text-sm text-muted">{user.email}</div></div>
            <Badge tone={user.role === "admin" ? "brand" : "gray"}>{user.role}</Badge>
          </div>
          <p className="mb-4 text-xs text-muted">{ROLE_HELP[user.role]}</p>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (name.trim()) rename.mutate(); }}>
            <label className="block"><Label>Display name</Label><Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} /></label>
            <ErrorNote error={rename.error} />
            <Button disabled={!name.trim() || name.trim() === user.name || rename.isPending}>Save</Button>
          </form>
        </Card>

        <Card className="lg:col-span-2">
          <CardTitle><Moon className="mr-1.5 inline h-4 w-4" />Appearance</CardTitle>
          <div role="radiogroup" aria-label="Colour theme" className="inline-flex rounded-lg bg-slate-100 p-1 text-sm">
            {([["system", "Match my device", Monitor], ["light", "Day", Sun], ["dark", "Night", Moon]] as [ThemePref, string, typeof Sun][]).map(([v, label, Icon]) => (
              <button key={v} role="radio" aria-checked={theme.pref === v} onClick={() => theme.set(v)} className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 ${theme.pref === v ? "bg-white font-medium shadow-sm" : "text-muted"}`}><Icon className="h-4 w-4" />{label}</button>))}
          </div>
          <p className="mt-2 text-xs text-muted">Saved in this browser only.</p>
        </Card>

        <Card>
          <CardTitle><KeyRound className="mr-1.5 inline h-4 w-4" />Password</CardTitle>
          {user.auth_source && user.auth_source !== "password" ? <p className="text-sm text-muted">You sign in through single sign-on, so there is no password here.</p> : (
            <form className="space-y-3" onSubmit={submit}>
              <label className="block"><Label>Current password</Label><Input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></label>
              <label className="block"><Label>New password</Label><Input type="password" autoComplete="new-password" minLength={8} value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></label>
              <label className="block"><Label>Repeat new password</Label><Input type="password" autoComplete="new-password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} /></label>
              {mismatch && <p role="alert" className="text-sm text-red-600">The passwords do not match.</p>}
              <ErrorNote error={change.error} />
              <Button disabled={!pw.current || pw.next.length < 8 || mismatch || !pw.again || change.isPending}>Change password</Button>
              <p className="text-xs text-muted">Changing your password signs you out everywhere else.</p>
            </form>)}
        </Card>

        {user.auth_source === "password" && <TwoFactor enabled={!!user.totp_enabled} onChange={(on) => setUser({ ...user, totp_enabled: on })} />}

        <Card className="lg:col-span-2">
          <CardTitle right={(sessions.data?.length ?? 0) > 1 ? <Button size="sm" variant="outline" disabled={others.isPending} onClick={async () => { if (await ask({ title: "Sign out everywhere else?", message: "Every other browser and device is signed out. This one stays signed in.", confirmLabel: "Sign out others", danger: true })) others.mutate(); }}>Sign out other devices</Button> : undefined}><ShieldCheck className="mr-1.5 inline h-4 w-4" />Where you are signed in</CardTitle>
          {!sessions.data?.length ? <Empty>No active sessions.</Empty> : (
            <ul aria-label="Sessions" className="divide-y divide-line">{sessions.data.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
                <Laptop className="h-5 w-5 text-muted" />
                <div className="min-w-0 flex-1"><div className="font-medium">{s.device}{s.current && <Badge tone="green" className="ml-2">this device</Badge>}</div>
                  <div className="text-xs text-muted">{s.ip || "unknown address"} · signed in {timeAgo(s.created_at)} · last active {timeAgo(s.last_seen_at)}{s.method === "sso" ? " · single sign-on" : ""}</div></div>
                {!s.current && <Button size="sm" variant="ghost" disabled={end.isPending} onClick={() => end.mutate(s.id)}>End session</Button>}
              </li>))}</ul>)}
          <ErrorNote error={end.error ?? others.error} />
        </Card>
      </div>
    </div>
  );
}


/** Optional second step at sign-in: a six-digit code from an authenticator app. */
function TwoFactor({ enabled, onChange }: { enabled: boolean; onChange: (on: boolean) => void }) {
  const toast = useToast();
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [off, setOff] = useState({ open: false, password: "", code: "" });
  const start = useMutation({ mutationFn: () => api<TotpSetup>("/auth/totp/setup", { method: "POST" }), onSuccess: setSetup });
  const confirm = useMutation({ mutationFn: () => api<{ recovery_codes: string[] }>("/auth/totp/enable", { method: "POST", json: { code } }), onSuccess: (r) => { setCodes(r.recovery_codes); setSetup(null); setCode(""); onChange(true); } });
  const disable = useMutation({ mutationFn: () => api("/auth/totp/disable", { method: "POST", json: { password: off.password, code: off.code } }), onSuccess: () => { setOff({ open: false, password: "", code: "" }); onChange(false); toast("Authenticator turned off"); } });
  return (
    <Card>
      <CardTitle right={enabled ? <Badge tone="green">on</Badge> : <Badge>off</Badge>}><Smartphone className="mr-1.5 inline h-4 w-4" />Authenticator app</CardTitle>
      <p className="mb-3 text-sm text-muted">Ask for a six-digit code from an app such as Google Authenticator, 1Password or Authy each time you sign in, so a stolen password is not enough.</p>
      {codes ? (
        <div role="status" className="space-y-2 rounded-lg bg-emerald-50 p-3 text-sm">
          <p className="font-medium text-emerald-900">Save these recovery codes now. Each works once if you lose your phone; they are not shown again.</p>
          <pre aria-label="Recovery codes" className="rounded bg-white p-2 font-mono text-xs">{codes.join("\n")}</pre>
          <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => { void navigator.clipboard?.writeText(codes.join("\n")); toast("Copied"); }}><Copy className="h-3.5 w-3.5" />Copy</Button><Button size="sm" onClick={() => setCodes(null)}>I have saved them</Button></div>
        </div>
      ) : enabled ? (off.open ? (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); disable.mutate(); }}>
          <Input type="password" aria-label="Your password to confirm" placeholder="Your password" value={off.password} onChange={(e) => setOff({ ...off, password: e.target.value })} />
          <Input aria-label="Code to confirm" placeholder="A current code or a recovery code" value={off.code} onChange={(e) => setOff({ ...off, code: e.target.value })} />
          <ErrorNote error={disable.error} />
          <div className="flex gap-2"><Button size="sm" variant="danger" disabled={!off.password || !off.code || disable.isPending}>Turn off</Button><Button size="sm" type="button" variant="outline" onClick={() => setOff({ open: false, password: "", code: "" })}>Cancel</Button></div>
        </form>) : <Button variant="outline" size="sm" onClick={() => setOff({ ...off, open: true })}>Turn off…</Button>
      ) : setup ? (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); confirm.mutate(); }}>
          <p className="text-sm">1. In your app choose “add account” and enter this key (or open the link on your phone):</p>
          <code aria-label="Setup key" className="block break-all rounded bg-slate-100 p-2 text-sm">{setup.secret}</code>
          <a className="text-xs text-brand hover:underline" href={setup.uri}>Open in an authenticator app</a>
          <p className="text-sm">2. Enter the six-digit code it shows:</p>
          <Input aria-label="Six-digit code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" />
          <ErrorNote error={confirm.error} />
          <div className="flex gap-2"><Button size="sm" disabled={code.replace(/\D/g, "").length !== 6 || confirm.isPending}>Turn on</Button><Button size="sm" type="button" variant="outline" onClick={() => setSetup(null)}>Cancel</Button></div>
        </form>
      ) : <><Button size="sm" disabled={start.isPending} onClick={() => start.mutate()}>Set up</Button><ErrorNote error={start.error} /></>}
    </Card>
  );
}
