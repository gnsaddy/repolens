import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, ErrorNote, Input, Label, Spinner } from "@/components/ui";
import { api, type GithubLoginSettings } from "@/lib/api";

/** Which e-mail domains may sign in to this workspace with GitHub. The OAuth app itself belongs to the server. */
export default function GithubLoginCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const q = useQuery({ queryKey: ["github-login"], queryFn: () => api<GithubLoginSettings>("/settings/github-login") });
  const c = q.data;
  const [domains, setDomains] = useState("");
  const [role, setRole] = useState("viewer");
  const [provision, setProvision] = useState(true);
  const [on, setOn] = useState(true);
  useEffect(() => {
    if (!c?.configured) return;
    setDomains((c.allowed_domains ?? []).join(", ")); setRole(c.default_role ?? "viewer"); setProvision(!!c.auto_provision); setOn(!!c.enabled);
  }, [c]);
  const save = useMutation({
    mutationFn: () => api<GithubLoginSettings>("/settings/github-login", { method: "PUT", json: { allowed_domains: domains, default_role: role, auto_provision: provision, enabled: on } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["github-login"] }); toast("GitHub sign-in saved"); },
  });
  const remove = useMutation({
    mutationFn: () => api("/settings/github-login", { method: "DELETE" }),
    onSuccess: () => { setDomains(""); qc.invalidateQueries({ queryKey: ["github-login"] }); toast("GitHub sign-in removed"); },
  });
  return (
    <Card aria-label="Sign in with GitHub">
      <CardTitle right={c && <Badge tone={c.configured && c.enabled && c.app_configured ? "green" : "gray"}>{c.configured && c.enabled && c.app_configured ? "on" : "off"}</Badge>}>Sign in with GitHub</CardTitle>
      {q.isLoading ? <Spinner /> : !c ? <ErrorNote error={q.error} /> : (
        <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <p className="text-muted">People whose <strong>verified</strong> GitHub e-mail address is in one of these domains can sign in with GitHub. An address GitHub has not verified is never used, and nobody becomes an admin this way.</p>
          {!c.app_configured && (
            <p role="note" className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800">This server has no GitHub OAuth app yet. Register one on GitHub (Settings, Developer settings, OAuth Apps) with the callback URL below, then set <code>REPOLENS_GITHUB_LOGIN_CLIENT_ID</code> and <code>REPOLENS_GITHUB_LOGIN_CLIENT_SECRET</code> on the server and restart it. The button appears on the sign-in page once both are set.</p>)}
          <p className="text-xs text-muted">Callback URL for the OAuth app: <code>{c.redirect_uri}</code></p>
          <label className="block"><Label>Allowed e-mail domains</Label><Input aria-label="Allowed e-mail domains" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="acme.io, acme.com" /></label>
          <label className="block"><Label>Role for new people</Label>
            <select aria-label="Role for new people" value={role} onChange={(e) => setRole(e.target.value)} className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm text-ink"><option value="viewer">Viewer</option><option value="member">Member</option></select></label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={provision} onChange={(e) => setProvision(e.target.checked)} />Create an account the first time someone signs in</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />Enabled</label>
          <ErrorNote error={save.error} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={save.isPending || !domains.trim()}>Save</Button>
            {c.configured && <Button type="button" size="sm" variant="outline" disabled={remove.isPending} onClick={async () => { if (await confirm({ title: "Turn off GitHub sign-in?", message: "People who signed in this way keep their accounts but must use another sign-in.", confirmLabel: "Remove", danger: true })) remove.mutate(); }}>Remove</Button>}
          </div>
        </form>)}
    </Card>
  );
}
