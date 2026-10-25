import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, ShieldOff, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import Modal from "@/components/Modal";
import { selectCls } from "@/components/ConnectionCard";
import { Badge, Button, Card, Empty, ErrorNote, Input, Label, PageHeader, Spinner } from "@/components/ui";
import { api, type Person } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

interface LinkResult { link: string; expires_at: string }

/** Everyone in the workspace: invite people, give someone a password-reset link, clear a lost authenticator. */
export default function People() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [invite, setInvite] = useState(false);
  const [shown, setShown] = useState<{ title: string; link: string; expires: string } | null>(null);
  const list = useQuery({ queryKey: ["people"], enabled: user?.role === "admin", queryFn: () => api<Person[]>("/auth/people") });
  const reset = useMutation({ mutationFn: (p: Person) => api<LinkResult>(`/auth/users/${p.id}/reset-link`, { method: "POST" }),
    onSuccess: (r, p) => { qc.invalidateQueries({ queryKey: ["people"] }); setShown({ title: `Reset link for ${p.email}`, link: r.link, expires: r.expires_at }); } });
  const mfa = useMutation({ mutationFn: (p: Person) => api(`/auth/users/${p.id}/reset-mfa`, { method: "POST" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["people"] }); toast("Authenticator removed. They can set it up again."); } });
  if (user?.role !== "admin") return <Empty>Only workspace admins can manage people.</Empty>;
  const full = (l: string) => `${window.location.origin}${l}`;
  return (
    <div>
      <PageHeader title="People" subtitle="Invite people, and help someone who forgot their password or lost their phone. Nothing is e-mailed: you hand over a one-time link."
        actions={<Button onClick={() => setInvite(true)}><UserPlus className="h-4 w-4" />Invite someone</Button>} />
      <ErrorNote error={reset.error ?? mfa.error} />
      {list.isLoading ? <Spinner /> : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm" aria-label="People">
            <thead className="bg-slate-50 text-left text-xs text-muted"><tr><th className="px-3 py-2 font-medium">Person</th><th className="font-medium">Role</th><th className="font-medium">Sign-in</th><th className="font-medium">Since</th><th /></tr></thead>
            <tbody>{list.data?.map((p) => (
              <tr key={p.id} className="border-t border-line">
                <td className="px-3 py-2"><div className="font-medium">{p.name || p.email}</div>{p.name && <div className="text-xs text-muted">{p.email}</div>}</td>
                <td><Badge tone={p.role === "admin" ? "brand" : "gray"}>{p.role}</Badge></td>
                <td className="space-x-1">{p.pending === "invite" ? <Badge tone="amber">invited, not joined yet</Badge> : p.auth_source !== "password" ? <Badge tone="blue">single sign-on</Badge> : <Badge>password</Badge>}{p.totp_enabled && <Badge tone="green">authenticator</Badge>}{p.pending === "reset" && <Badge tone="amber">reset link waiting</Badge>}</td>
                <td className="text-xs text-muted">{timeAgo(p.created_at)}</td>
                <td className="space-x-1 text-right">
                  {p.auth_source === "password" && <Button size="sm" variant="ghost" disabled={reset.isPending} onClick={() => reset.mutate(p)}><KeyRound className="h-3.5 w-3.5" />{p.pending === "invite" ? "New invite link" : "Reset password"}</Button>}
                  {p.totp_enabled && <Button size="sm" variant="ghost" disabled={mfa.isPending} onClick={async () => { if (await ask({ title: `Remove ${p.email}'s authenticator?`, message: "They can sign in with just their password until they set one up again, and any other device of theirs is signed out.", confirmLabel: "Remove", danger: true })) mfa.mutate(p); }}><ShieldOff className="h-3.5 w-3.5" />Remove authenticator</Button>}
                </td>
              </tr>))}</tbody>
          </table>
        </Card>)}
      {invite && <InviteModal onClose={() => setInvite(false)} onDone={(r) => { setInvite(false); qc.invalidateQueries({ queryKey: ["people"] }); setShown({ title: `Invitation for ${r.email}`, link: r.link, expires: r.expires_at }); }} />}
      {shown && (
        <Modal title={shown.title} subtitle="Give them this link. It works once, and is shown only now." onClose={() => setShown(null)}>
          <div className="space-y-3">
            <Input aria-label="One-time link" readOnly value={full(shown.link)} onFocus={(e) => e.currentTarget.select()} className="font-mono text-xs" />
            <p className="text-xs text-muted">Valid until {new Date(shown.expires).toLocaleString()}. Anyone who has the link can set the password, so send it privately.</p>
            <Button onClick={() => { void navigator.clipboard?.writeText(full(shown.link)); toast("Link copied"); }}><Copy className="h-4 w-4" />Copy link</Button>
          </div>
        </Modal>)}
    </div>
  );
}

function InviteModal({ onClose, onDone }: { onClose: () => void; onDone: (r: LinkResult & { email: string }) => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("member");
  const send = useMutation({ mutationFn: () => api<{ person: Person; link: string; expires_at: string }>("/auth/invites", { method: "POST", json: { email, name, role } }), onSuccess: (r) => onDone({ link: r.link, expires_at: r.expires_at, email: r.person.email }) });
  return (
    <Modal title="Invite someone" subtitle="They choose their own password through a one-time link." onClose={onClose}>
      <form className="space-y-3" onSubmit={(e: FormEvent) => { e.preventDefault(); send.mutate(); }}>
        <label className="block"><Label>E-mail</Label><Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" /></label>
        <label className="block"><Label>Name (optional)</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="block"><Label>Role</Label><select className={selectCls} value={role} onChange={(e) => setRole(e.target.value)}><option value="viewer">Viewer: read and search</option><option value="member">Member: add repositories, investigate, propose edits</option><option value="admin">Admin: everything, including settings and people</option></select></label>
        <ErrorNote error={send.error} />
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button disabled={!email || send.isPending}>{send.isPending ? "Creating…" : "Create invitation"}</Button></div>
      </form>
    </Modal>
  );
}
