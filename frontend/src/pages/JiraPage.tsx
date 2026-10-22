import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, PageHeader } from "@/components/ui";
import JiraIssuePanel from "@/components/JiraIssuePanel";
import { api, type JiraConnection, type JiraIssue } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";

export default function JiraPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const conn = useQuery({ queryKey: ["jira-conn"], queryFn: () => api<JiraConnection | null>("/jira/connection") });
  const [proj, setProj] = useState("");
  const [status, setStatus] = useState("open");
  const [type, setType] = useState("Bug");
  const [text, setText] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [custom, setCustom] = useState<string | null>(null);
  const built = [proj && `project = "${proj}"`, status === "open" ? "statusCategory != Done" : status === "done" ? "statusCategory = Done" : "", type && `issuetype = "${type}"`, text.trim() && `text ~ "${text.trim().replace(/["\\]/g, " ")}"`].filter(Boolean).join(" AND ") + " ORDER BY updated DESC";
  const jql = custom ?? built;
  const setJql = (v: string) => setCustom(v);
  const projects = useQuery({ queryKey: ["jira-projects"], enabled: !!conn.data, retry: false, queryFn: () => api<{ key: string; name: string }[]>("/jira/projects") });
  const issues = useQuery({ queryKey: ["jira-issues", jql], enabled: !!conn.data, retry: false, queryFn: () => api<JiraIssue[]>(`/jira/issues?jql=${encodeURIComponent(jql)}`) });
  const investigate = useMutation({ mutationFn: (key: string) => api<{ investigation_id: string }>("/jira/investigate", { method: "POST", json: { issue_key: key, repo_ids: [] } }),
    onSuccess: (r) => nav(`/investigations/${r.investigation_id}`) });

  return (
    <div>
      <PageHeader title="Jira Integration" subtitle="Read tickets and their comments, analyse them, investigate the cause in your code and docs, and publish approved reports back." />
      {!conn.isLoading && !conn.data ? (
        <Card className="mx-auto max-w-xl text-center">
          <h2 className="text-base font-semibold">Connect Jira first</h2>
          <p className="mt-1 text-sm text-muted">Add your Jira site and an API token in Settings, then come back to browse bugs, investigate them and publish approved reports.</p>
          <Link to="/settings" className="mt-4 inline-flex h-9 items-center rounded-md bg-brand px-4 text-sm font-medium text-white hover:bg-brand-600">Open Settings</Link>
        </Card>
      ) : (<div className="space-y-4">
        <p className="text-sm text-muted">Connected to <span className="font-medium text-ink">{conn.data?.base_url}</span>. <Link to="/settings" className="text-brand hover:underline">Manage connection</Link></p>
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
                  <tr key={i.key} className="border-t border-line"><td className="py-2 font-medium">{i.key}</td>
                    <td className="max-w-xs truncate"><button className="text-left hover:text-brand hover:underline" onClick={() => setOpenKey(i.key)} title="Read the ticket and analyse it">{i.summary}</button></td><td><Badge>{i.status}</Badge></td>
                    <td className="space-x-2 text-right"><Button size="sm" variant="ghost" onClick={() => setOpenKey(i.key)}>Open</Button>{i.investigation_id ? <Link className="text-xs text-brand hover:underline" to={`/investigations/${i.investigation_id}`}>View RCA</Link> :
                      canWrite(user) && <Button size="sm" variant="outline" disabled={investigate.isPending} onClick={() => investigate.mutate(i.key)}>Investigate</Button>}</td></tr>))}</tbody></table>)}
          </>)}
        </Card>
      </div>)}
      {openKey && <JiraIssuePanel issueKey={openKey} canInvestigate={canWrite(user)} onClose={() => setOpenKey(null)} />}
    </div>
  );
}
