import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plug } from "lucide-react";
import { useState } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { selectCls } from "@/components/ConnectionCard";
import { Badge, Button, Card, CardTitle, ErrorNote, Input } from "@/components/ui";
import { api, type AccessToken } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

/** Personal access tokens for scripts, CI and editors (MCP), with copy-paste setup for the common clients. */
export default function AccessTokens({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ask = useConfirm();
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [days, setDays] = useState("90");
  const [fresh, setFresh] = useState<{ name: string; token: string } | null>(null);
  const list = useQuery({ queryKey: ["api-tokens"], queryFn: () => api<AccessToken[]>("/auth/api-tokens") });
  const make = useMutation({
    mutationFn: () => api<AccessToken & { token: string }>("/auth/api-tokens", { method: "POST", json: { name, scope, days: days === "never" ? null : Number(days) } }),
    onSuccess: (r) => { setFresh({ name: r.name, token: r.token }); setName(""); qc.invalidateQueries({ queryKey: ["api-tokens"] }); },
  });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/auth/api-tokens/${id}`, { method: "DELETE" }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["api-tokens"] }); toast("Token revoked"); } });
  const url = `${window.location.origin}/api/mcp`;
  const shown = fresh?.token ?? "rlp_YOUR_TOKEN";
  const active = (Array.isArray(list.data) ? list.data : []).filter((t) => !t.revoked);
  return (
    <Card className="lg:col-span-2">
      <CardTitle><KeyRound className="mr-1.5 inline h-4 w-4" />Access tokens</CardTitle>
      <p className="mb-3 text-sm text-muted">For scripts, CI and editor tools such as Claude Code or Cursor. A token acts as you, with less power: <b>read</b> can search, ask and look things up; <b>write</b> can also start investigations. Neither can change settings or people.</p>
      {fresh && (
        <div role="status" className="mb-3 space-y-2 rounded-lg bg-emerald-50 p-3 text-sm">
          <p className="font-medium text-emerald-900">Copy “{fresh.name}” now. It is shown only once.</p>
          <div className="flex gap-2"><Input readOnly aria-label="New access token" value={fresh.token} className="font-mono text-xs" onFocus={(e) => e.currentTarget.select()} />
            <Button size="sm" variant="outline" onClick={() => { void navigator.clipboard?.writeText(fresh.token); toast("Copied"); }}><Copy className="h-3.5 w-3.5" />Copy</Button>
            <Button size="sm" variant="ghost" onClick={() => setFresh(null)}>Done</Button></div>
        </div>)}
      <form className="mb-4 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); make.mutate(); }}>
        <label className="text-xs text-muted">Name<Input aria-label="Token name" className="mt-1 w-52" placeholder="e.g. Claude Code on my laptop" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="text-xs text-muted">Can<select aria-label="Token access" className={`${selectCls} mt-1`} value={scope} onChange={(e) => setScope(e.target.value as "read" | "write")}><option value="read">Read (search, ask)</option>{canWrite && <option value="write">Read and start investigations</option>}</select></label>
        <label className="text-xs text-muted">Expires<select aria-label="Token expiry" className={`${selectCls} mt-1`} value={days} onChange={(e) => setDays(e.target.value)}><option value="30">in 30 days</option><option value="90">in 90 days</option><option value="365">in a year</option><option value="never">never</option></select></label>
        <Button disabled={!name.trim() || make.isPending}>Create token</Button>
      </form>
      <ErrorNote error={make.error ?? revoke.error} />
      {active.length > 0 && (
        <ul aria-label="Access tokens" className="mb-4 divide-y divide-line text-sm">{active.map((t) => (
          <li key={t.id} className="flex flex-wrap items-center gap-2 py-2">
            <span className="font-medium">{t.name}</span><code className="text-xs text-muted">{t.prefix}…</code><Badge tone={t.scope === "write" ? "amber" : "gray"}>{t.scope}</Badge>
            <span className="text-xs text-muted">{t.last_used_at ? `used ${timeAgo(t.last_used_at)}` : "never used"}{t.expires_at ? ` · expires ${new Date(t.expires_at).toLocaleDateString()}` : " · does not expire"}</span>
            <Button size="sm" variant="ghost" className="ml-auto" onClick={async () => { if (await ask({ title: `Revoke “${t.name}”?`, message: "Anything using it stops working immediately.", confirmLabel: "Revoke", danger: true })) revoke.mutate(t.id); }}>Revoke</Button>
          </li>))}</ul>)}
      <details open className="rounded-lg border border-line p-3 text-sm">
        <summary className="flex cursor-pointer items-center gap-1.5 font-medium"><Plug className="h-4 w-4" />Use RepoLens from your editor (MCP)</summary>
        <div className="mt-3 space-y-3 text-xs">
          <p className="text-muted">RepoLens speaks the Model Context Protocol, so an editor can search your code, docs and pull requests, ask questions with cited sources, and check a symbol before changing it, using your access. Replace the token with yours.</p>
          <div><div className="mb-1 font-medium">Claude Code</div><pre className="overflow-x-auto rounded bg-slate-900 p-2 font-mono text-slate-100">{`claude mcp add --transport http repolens ${url} --header "Authorization: Bearer ${shown}"`}</pre></div>
          <div><div className="mb-1 font-medium">Cursor / other clients that accept a URL (.cursor/mcp.json)</div><pre className="overflow-x-auto rounded bg-slate-900 p-2 font-mono text-slate-100">{JSON.stringify({ mcpServers: { repolens: { url, headers: { Authorization: `Bearer ${shown}` } } } }, null, 2)}</pre></div>
          <div><div className="mb-1 font-medium">Claude Desktop (standard input/output bridge)</div><pre className="overflow-x-auto rounded bg-slate-900 p-2 font-mono text-slate-100">{JSON.stringify({ mcpServers: { repolens: { command: "python", args: ["-m", "repolens.devtools.mcp_stdio", "--url", url], env: { REPOLENS_TOKEN: shown } } } }, null, 2)}</pre></div>
        </div>
      </details>
    </Card>
  );
}
