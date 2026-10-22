import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, Label, PageHeader } from "@/components/ui";
import { api, type JiraConnection, type JiraIssue } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";

export default function JiraPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const nav = useNavigate();
  const conn = useQuery({ queryKey: ["jira-conn"], queryFn: () => api<JiraConnection | null>("/jira/connection") });
  const [f, setF] = useState({ base_url: "", deployment: "cloud", email: "", token: "", allow_publish: false });
  const [proj, setProj] = useState("");
  const [status, setStatus] = useState("open");
  const [type, setType] = useState("Bug");
  const [text, setText] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [custom, setCustom] = useState<string | null>(null);
  const built = [proj && `project = "${proj}"`, status === "open" ? "statusCategory != Done" : status === "done" ? "statusCategory = Done" : "", type && `issuetype = "${type}"`, text.trim() && `text ~ "${text.trim().replace(/["\\]/g, " ")}"`].filter(Boolean).join(" AND ") + " ORDER BY updated DESC";
  const jql = custom ?? built;
  const setJql = (v: string) => setCustom(v);
  const save = useMutation({ mutationFn: () => api<JiraConnection>("/jira/connection", { method: "PUT", json: f }),
    onSuccess: () => { setF({ ...f, token: "" }); qc.invalidateQueries({ queryKey: ["jira-conn"] }); } });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; user?: string; error?: string }>("/jira/test", { method: "POST" }) });
  const projects = useQuery({ queryKey: ["jira-projects"], enabled: !!conn.data, retry: false, queryFn: () => api<{ key: string; name: string }[]>("/jira/projects") });
  const issues = useQuery({ queryKey: ["jira-issues", jql], enabled: !!conn.data, retry: false, queryFn: () => api<JiraIssue[]>(`/jira/issues?jql=${encodeURIComponent(jql)}`) });
  const investigate = useMutation({ mutationFn: (key: string) => api<{ investigation_id: string }>("/jira/investigate", { method: "POST", json: { issue_key: key, repo_ids: [] } }),
    onSuccess: (r) => nav(`/investigations/${r.investigation_id}`) });
  const admin = user?.role === "admin";
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };

  return (
    <div>
      <PageHeader title="Jira Integration" subtitle="Fetch bug tickets, investigate them, and publish approved RCA reports back as comments." />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <Card>
          <CardTitle right={conn.data && <Badge tone="green">connected</Badge>}>Connection</CardTitle>
          {admin ? (
            <form onSubmit={submit} className="space-y-3">
              <label className="block"><Label>Jira URL</Label><Input required value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder={conn.data?.base_url ?? "https://acme.atlassian.net"} /></label>
              <label className="block"><Label>Deployment</Label><select className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value })}>
                <option value="cloud">Jira Cloud (email + API token)</option><option value="datacenter">Data Center (personal access token)</option></select></label>
              {f.deployment === "cloud" && <label className="block"><Label>Account email</Label><Input type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>}
              <label className="block"><Label>{f.deployment === "cloud" ? "API token" : "Personal access token"}</Label><Input type="password" required autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} placeholder="Stored encrypted; never shown again" /></label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.allow_publish} onChange={(e) => setF({ ...f, allow_publish: e.target.checked })} />Allow publishing RCA comments (after approval)</label>
              <ErrorNote error={save.error} />
              <div className="flex gap-2"><Button disabled={save.isPending}>Save</Button>
                <Button type="button" variant="outline" disabled={!conn.data || test.isPending} onClick={() => test.mutate()}>Test</Button></div>
              {test.data && <p className={`text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? `Connected as ${test.data.user}` : test.data.error}</p>}
            </form>) : <p className="text-sm text-muted">{conn.data ? `Connected to ${conn.data.base_url}` : "An admin must configure Jira."}</p>}
        </Card>
        <Card>
          <CardTitle>Issues</CardTitle>
          {!conn.data ? <Empty>Configure the connection to browse issues.</Empty> : (<>
            <div className="mb-3 grid gap-2 sm:grid-cols-4">
              <select aria-label="Project" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={proj} onChange={(e) => { setCustom(null); setProj(e.target.value); }}>
                <option value="">All projects</option>{(Array.isArray(projects.data) ? projects.data : []).map((p) => <option key={p.key} value={p.key}>{p.name} ({p.key})</option>)}</select>
              <select aria-label="Status" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={status} onChange={(e) => { setCustom(null); setStatus(e.target.value); }}>
                <option value="open">Open</option><option value="done">Done</option><option value="any">Any status</option></select>
              <select aria-label="Type" className="h-9 rounded-md border border-line bg-white px-2 text-sm" value={type} onChange={(e) => { setCustom(null); setType(e.target.value); }}>
                <option value="Bug">Bugs</option><option value="Incident">Incidents</option><option value="">Any type</option></select>
              <Input aria-label="Search text" placeholder="Text…" value={text} onChange={(e) => { setCustom(null); setText(e.target.value); }} />
            </div>
            <div className="mb-3 flex items-center gap-2 text-xs text-muted">
              <button className="text-brand hover:underline" onClick={() => setAdvanced((v) => !v)} aria-expanded={advanced}>{advanced ? "Hide JQL" : "Edit JQL"}</button>
              <Link className="ml-auto text-brand hover:underline" to={`/wiki?tab=sources&connector=jira&jql=${encodeURIComponent(jql)}`}>Import these as searchable history</Link></div>
            {advanced && <Input value={jql} onChange={(e) => setJql(e.target.value)} aria-label="JQL" className="mb-3 font-mono text-xs" />}
            <ErrorNote error={issues.error ?? investigate.error} />
            {!issues.data?.length ? <Empty>{issues.isLoading ? "Loading…" : "No issues."}</Empty> : (
              <table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-medium">Key</th><th className="font-medium">Summary</th><th className="font-medium">Status</th><th /></tr></thead>
                <tbody>{issues.data.map((i) => (
                  <tr key={i.key} className="border-t border-line"><td className="py-2"><a className="text-brand hover:underline" href={i.url} target="_blank" rel="noreferrer">{i.key}</a></td>
                    <td className="max-w-xs truncate">{i.summary}</td><td><Badge>{i.status}</Badge></td>
                    <td className="text-right">{i.investigation_id ? <Link className="text-xs text-brand hover:underline" to={`/investigations/${i.investigation_id}`}>View RCA</Link> :
                      canWrite(user) && <Button size="sm" variant="outline" disabled={investigate.isPending} onClick={() => investigate.mutate(i.key)}>Investigate</Button>}</td></tr>))}</tbody></table>)}
          </>)}
        </Card>
      </div>
    </div>
  );
}
