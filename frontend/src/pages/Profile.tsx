import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Laptop, LogOut, ShieldCheck, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Label, PageHeader } from "@/components/ui";
import { api, type SessionRow, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

const ROLE_HELP: Record<string, string> = { admin: "Can configure integrations, models and access, and approve changes.", member: "Can add repositories, investigate and propose edits.", viewer: "Can read and search; cannot change anything." };

export default function Profile() {
  const { user, setUser, logout } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
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
