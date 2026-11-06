import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, ErrorNote, Input, Label, Spinner } from "@/components/ui";
import { api, type GithubLoginSettings } from "@/lib/api";

/** Sign in with GitHub for this workspace. People who already have an account need nothing here (the server's OAuth app
 *  is enough); an admin can switch it off for the workspace, or let people from some e-mail domains get an account. */
export default function GithubLoginCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const q = useQuery({ queryKey: ["github-login"], queryFn: () => api<GithubLoginSettings>("/settings/github-login") });
  const c = q.data;
  const [domains, setDomains] = useState("");
  const [role, setRole] = useState("viewer");
  const [on, setOn] = useState(true);
  useEffect(() => {
    if (!c?.configured) return;
    setDomains((c.allowed_domains ?? []).join(", ")); setRole(c.default_role ?? "viewer"); setOn(!!c.enabled);
  }, [c]);
  const save = useMutation({
    mutationFn: () => api<GithubLoginSettings>("/settings/github-login", { method: "PUT", json: { allowed_domains: domains, default_role: role, enabled: on } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["github-login"] }); toast("GitHub sign-in saved"); },
  });
  const remove = useMutation({
    mutationFn: () => api("/settings/github-login", { method: "DELETE" }),
    onSuccess: () => { setDomains(""); setOn(true); qc.invalidateQueries({ queryKey: ["github-login"] }); toast("Back to the defaults"); },
  });
  const working = !!c?.app_configured && c.enabled !== false;
  return (
    <Card aria-label="Sign in with GitHub">
      <CardTitle right={c && <Badge tone={working ? "green" : "gray"}>{working ? "on" : "off"}</Badge>}>Sign in with GitHub</CardTitle>
      {q.isLoading ? <Spinner /> : !c ? <ErrorNote error={q.error} /> : (
        <form className="space-y-3 body-6" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <p className="text-muted">Once the server has a GitHub OAuth app, people who already have an account can use <strong>Continue with GitHub</strong> on the sign-in page: a <strong>verified</strong> e-mail address on their GitHub account must match their account. Nothing else needs to be set here.</p>
          {!c.app_configured && (
            <p role="note" className="rounded-lg bg-amber-50 p-2 caption-5 text-amber-800">This server has no GitHub OAuth app yet. Register one on GitHub (Settings, Developer settings, OAuth Apps) with the callback URL below, then set <code>REPOLENS_GITHUB_LOGIN_CLIENT_ID</code> and <code>REPOLENS_GITHUB_LOGIN_CLIENT_SECRET</code> on the server and restart it. The button appears on the sign-in page once both are set.</p>)}
          <p className="caption-5 text-muted">Callback URL for the OAuth app: <code>{c.redirect_uri}</code></p>
          <label className="flex items-center gap-2"><input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />Allow GitHub sign-in for people in this workspace</label>
          <fieldset className="space-y-3 rounded-lg border border-line p-3">
            <legend className="px-1 caption-4 text-muted">Optional: create accounts for new people</legend>
            <label className="block"><Label>E-mail domains that get an account on first sign-in</Label><Input aria-label="E-mail domains that get an account" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="acme.io, acme.com (leave empty for existing accounts only)" /></label>
            <label className="block"><Label>Role for new people</Label>
              <select aria-label="Role for new people" value={role} onChange={(e) => setRole(e.target.value)} className="h-9 w-full rounded-lg border border-line bg-white px-2 body-6 text-ink"><option value="viewer">Viewer</option><option value="member">Member</option></select></label>
            <p className="caption-5 text-muted">New people need a <strong>verified</strong> GitHub address in one of these domains. Nobody becomes an admin this way.</p>
          </fieldset>
          <ErrorNote error={save.error} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={save.isPending}>Save</Button>
            {c.configured && <Button type="button" size="sm" variant="outline" disabled={remove.isPending} onClick={async () => { if (await confirm({ title: "Go back to the defaults?", message: "Accounts created this way are kept. People with an account can still sign in with GitHub; no new accounts are created.", confirmLabel: "Reset", danger: true })) remove.mutate(); }}>Reset</Button>}
          </div>
        </form>)}
    </Card>
  );
}
