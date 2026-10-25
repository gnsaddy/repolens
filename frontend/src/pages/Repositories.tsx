import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useConfirm } from "@/components/feedback";
import { File, GitPullRequest, Lock, RefreshCw, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Badge, Button, Card, CardTitle, CodeBlock, Empty, ErrorNote, Input, Label, PageHeader, statusTone } from "@/components/ui";
import StoragePanel from "@/components/StoragePanel";
import { api, type BranchList, type GithubConn, type IndexRunRow, type PullRow, type FileView, type PathOwners, type Repo, type SearchHit } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import AccessPanel from "@/components/AccessPanel";
import { timeAgo, safeHref } from "@/lib/utils";

export default function Repositories() {
  const ask = useConfirm();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [sel, setSel] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; line?: [number, number] } | null>(null);
  const [form, setForm] = useState({ name: "", source: "local", url: "" });
  const [picked, setPicked] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [searchQ, setSearchQ] = useState("");

  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories"),
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === "indexing" || r.status === "pending") ? 2000 : false) });
  const add = useMutation({ mutationFn: () => api<Repo>("/repositories", { method: "POST", json: { ...form, branches: picked } }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["repos"] }); setForm({ name: "", source: "local", url: "" }); setPicked([]); branchList.reset(); setSel(r.id); } });
  const branchList = useMutation({ mutationFn: () => api<BranchList>(`/repositories/branches?source=${form.source}&url=${encodeURIComponent(form.url)}`),
    onSuccess: (b) => setPicked(b.default ? [b.default] : []) });
  const togglePick = (b: string) => setPicked((cur) => (cur.includes(b) ? cur.filter((x) => x !== b) : [...cur, b]));
  const reindex = useMutation({ mutationFn: (id: string) => api(`/repositories/${id}/index`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["repos"] }) });
  const remove = useMutation({ mutationFn: (id: string) => api(`/repositories/${id}`, { method: "DELETE" }),
    onSuccess: () => { setSel(null); qc.invalidateQueries({ queryKey: ["repos"] }); } });

  const repo = repos.data?.find((r) => r.id === sel);
  const tree = useQuery({ queryKey: ["tree", sel], enabled: !!sel && repo?.status === "ready", queryFn: () => api<{ path: string; lines: number; language: string }[]>(`/repositories/${sel}/tree`) });
  const fileQ = useQuery({ queryKey: ["file", sel, file?.path], enabled: !!sel && !!file, queryFn: () => api<FileView>(`/repositories/${sel}/file?path=${encodeURIComponent(file!.path)}`) });
  const owners = useQuery({ queryKey: ["owners", sel, file?.path], enabled: !!sel && !!file && repo?.source !== "docs", queryFn: () => api<PathOwners>(`/ownership/path?repo_id=${sel}&path=${encodeURIComponent(file!.path)}`) });
  const commits = useQuery({ queryKey: ["commits", sel], enabled: !!sel && repo?.status === "ready", queryFn: () => api<{ sha: string; author: string; committed_at: string; message: string }[]>(`/repositories/${sel}/commits?limit=8`) });
  const gh = useQuery({ queryKey: ["github-conn"], queryFn: () => api<GithubConn>("/github/connection") });
  const runs = useQuery({ queryKey: ["runs", sel], enabled: !!sel && repo?.source !== "docs", queryFn: () => api<IndexRunRow[]>(`/repositories/${sel}/runs`), refetchInterval: 4000 });
  const prRunning = runs.data?.some((r) => r.commit_sha === "pull-requests" && r.status === "running") ?? false;
  const pulls = useQuery({ queryKey: ["pulls", sel], enabled: !!sel && repo?.source !== "docs", queryFn: () => api<PullRow[]>(`/repositories/${sel}/pulls`), refetchInterval: prRunning ? 2000 : false });
  const [ghRepo, setGhRepo] = useState("");
  const syncPrs = useMutation({ mutationFn: () => api(`/repositories/${sel}/sync-prs`, { method: "POST", json: ghRepo ? { github_repo: ghRepo } : {} }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pulls", sel] }); qc.invalidateQueries({ queryKey: ["runs", sel] }); } });
  const lastRun = runs.data?.find((r) => r.commit_sha !== "pull-requests" && r.finished_at);
  const prRun = runs.data?.find((r) => r.commit_sha === "pull-requests");
  const hits = useQuery({ queryKey: ["search", sel, searchQ], enabled: searchQ.length > 1, queryFn: () => api<SearchHit[]>(`/search?q=${encodeURIComponent(searchQ)}${sel ? `&repo_id=${sel}` : ""}`) });

  function submit(e: FormEvent) { e.preventDefault(); add.mutate(); }
  function search(e: FormEvent) { e.preventDefault(); setSearchQ(q.trim()); }

  return (
    <div>
      <PageHeader title="Repository Explorer" subtitle="Search and explore your indexed codebase with semantic understanding." />
      <form onSubmit={search} className="mb-6 flex gap-2">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search files, symbols, or ask a question…" aria-label="Search code" />
        <Button type="submit" variant="outline">Search{sel ? " in repo" : " all"}</Button>
      </form>
      {searchQ && (
        <Card className="mb-6"><CardTitle right={<button className="text-xs text-muted" onClick={() => { setSearchQ(""); setQ(""); }}>Clear</button>}>Results for “{searchQ}”</CardTitle>
          {!hits.data?.length ? <Empty>{hits.isLoading ? "Searching…" : "No matches."}</Empty> : (
            <ul className="space-y-3">{hits.data.map((h, i) => (
              <li key={i}><button className="text-left text-sm font-medium text-brand hover:underline" onClick={() => { setSel(h.repo_id); setFile({ path: h.file, line: [h.start_line, h.end_line] }); }}>
                {h.repo}:{h.file}</button><span className="ml-2 text-xs text-muted">{h.parent ? `${h.parent}.` : ""}{h.symbol} · lines {h.start_line}–{h.end_line}{h.symbol_match && " · symbol match"}</span>
                <pre className="mt-1 max-h-24 overflow-hidden rounded bg-slate-50 p-2 font-mono text-xs">{h.snippet}</pre></li>))}</ul>)}
        </Card>)}
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardTitle>Repositories</CardTitle>
            {!repos.data?.length ? <Empty>No repositories yet.</Empty> : (
              <ul className="space-y-1">{repos.data.map((r) => (
                <li key={r.id}><button onClick={() => { setSel(r.id); setFile(null); }} aria-current={sel === r.id}
                  className={`flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm hover:bg-slate-50 ${sel === r.id ? "bg-indigo-50" : ""}`}>
                  <span className="flex min-w-0 items-center gap-1.5 truncate">{r.visibility === "restricted" && <Lock className="h-3 w-3 shrink-0 text-amber-600" aria-label="restricted" />}<span className="truncate">{r.name}</span></span><Badge tone={statusTone(r.status)}>{r.status}</Badge></button></li>))}</ul>)}
          </Card>
          {user?.role === "admin" && <StoragePanel />}
          {canWrite(user) && (
            <Card>
              <CardTitle>Add repository</CardTitle>
              <form onSubmit={submit} className="space-y-3">
                <label className="block"><Label>Name</Label><Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="user-service" /></label>
                <label className="block"><Label>Source</Label>
                  <select className="h-9 w-full rounded-md border border-line bg-white px-2 text-sm" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                    <option value="local">Local path (server)</option><option value="git">Git URL (https/ssh)</option></select></label>
                <label className="block"><Label>{form.source === "local" ? "Path" : "Clone URL"}</Label><Input required value={form.url} onChange={(e) => { setForm({ ...form, url: e.target.value }); branchList.reset(); setPicked([]); }} placeholder={form.source === "local" ? "/srv/repos/user-service" : "https://github.com/org/repo.git"} /></label>
                <div>
                  <Button type="button" size="sm" variant="outline" disabled={!form.url || branchList.isPending} onClick={() => branchList.mutate()}>{branchList.isPending ? "Loading…" : "Choose branches"}</Button>
                  <ErrorNote error={branchList.error} />
                  {branchList.data && (
                    <fieldset className="mt-2 max-h-40 space-y-1 overflow-auto rounded-md border border-line p-2">
                      <legend className="px-1 text-xs text-muted">Each selected branch is indexed separately (max 8)</legend>
                      {branchList.data.branches.map((b) => (
                        <label key={b} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={picked.includes(b)} onChange={() => togglePick(b)} />{b}{b === branchList.data.default && <Badge>default</Badge>}</label>))}
                    </fieldset>)}
                  {!branchList.data && <p className="mt-1 text-xs text-muted">Without a choice, the default branch is indexed.</p>}
                </div>
                <ErrorNote error={add.error} />
                <Button className="w-full" disabled={add.isPending}>Add &amp; index</Button>
              </form>
            </Card>)}
        </div>
        <div className="min-w-0 space-y-4">
          {!repo ? <Empty>Select a repository to explore its files and history.</Empty> : (<>
            <Card>
              <CardTitle right={canWrite(user) && (<div className="flex gap-1">
                <Button size="sm" variant="outline" onClick={() => reindex.mutate(repo.id)} disabled={repo.status === "indexing"}><RefreshCw className="h-3.5 w-3.5" />Re-index</Button>
                {user?.role === "admin" && <Button size="sm" variant="ghost" aria-label="Delete repository" onClick={async () => { if (await ask({ title: `Delete ${repo.name}?`, message: "Its index, graph, investigations and clone are removed. The source repository is not touched.", confirmLabel: "Delete repository", danger: true })) remove.mutate(repo.id); }}><Trash2 className="h-3.5 w-3.5" /></Button>}</div>)}>{repo.name}</CardTitle>
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
                <span>{repo.file_count} files</span><span>{repo.chunk_count} chunks</span><span>branch {repo.branch || repo.default_branch}</span>{lastRun?.finished_at && <span>last index {Math.max(1, Math.round((+new Date(lastRun.finished_at) - +new Date(lastRun.started_at)) / 1000))}s</span>}
                <span>revision {repo.indexed_sha?.slice(0, 10) ?? "—"}</span><span>updated {timeAgo(repo.updated_at)}</span></div>
              {!!runs.data?.[0]?.secrets_redacted && <p className="mt-2 text-xs text-amber-700">The last index removed {runs.data[0].secrets_redacted} apparent secret(s) from stored code, diffs and docs.</p>}
              {repo.status === "error" && <p className="mt-2 text-sm text-red-600">Indexing failed — check the path/URL and re-index.</p>}
            </Card>
            {user?.role === "admin" && <AccessPanel key={repo.id} repoId={repo.id} repoName={repo.name} />}
            <div className="grid gap-4 xl:grid-cols-[260px_1fr]">
              <Card className="max-h-[32rem] overflow-auto p-3">
                <CardTitle>Files</CardTitle>
                <ul className="space-y-0.5">{tree.data?.map((f) => (
                  <li key={f.path}><button onClick={() => setFile({ path: f.path })} className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-slate-50 ${file?.path === f.path ? "bg-indigo-50" : ""}`}>
                    <File className="h-3.5 w-3.5 shrink-0 text-muted" /><span className="truncate">{f.path}</span></button></li>))}</ul>
              </Card>
              <div className="min-w-0 space-y-4">
                {file && (<Card>
                  <CardTitle>{file.path}</CardTitle>
                  {!!owners.data?.owners.length && <p className="-mt-2 mb-3 text-xs text-muted">Maintained by {owners.data.owners.map((o) => `${o.author} (${Math.round(o.share * 100)}%)`).join(", ")}</p>}
                  {fileQ.error ? <ErrorNote error={fileQ.error} /> : fileQ.data && (<div className="max-h-[32rem] overflow-auto"><CodeBlock code={fileQ.data.content} highlight={file.line} /></div>)}
                </Card>)}
                {repo.source !== "docs" && (
                  <Card>
                    <CardTitle right={canWrite(user) && gh.data?.connected && (
                      <div className="flex items-center gap-2">
                        <Input className="h-8 w-44 text-xs" placeholder="owner/name (if not detected)" value={ghRepo} onChange={(e) => setGhRepo(e.target.value)} aria-label="GitHub repository" />
                        <Button size="sm" variant="outline" disabled={syncPrs.isPending || prRun?.status === "running"} onClick={() => syncPrs.mutate()}><GitPullRequest className="h-3.5 w-3.5" />Sync PRs</Button></div>)}>Pull requests</CardTitle>
                    <ErrorNote error={syncPrs.error} />
                    {prRun?.status === "failed" && <p className="mb-2 text-xs text-red-600">Last sync failed: {prRun.error}</p>}
                    {!gh.data?.connected && <p className="text-xs text-muted">Connect GitHub under Settings to ingest pull requests.</p>}
                    {gh.data?.connected && !pulls.data?.length && <Empty>No pull requests synced yet.</Empty>}
                    <ul className="space-y-2 text-sm">{pulls.data?.slice(0, 10).map((p) => (
                      <li key={p.number} className="flex items-center gap-2"><Badge tone={p.state === "merged" ? "green" : p.state === "open" ? "blue" : "gray"}>{p.state}</Badge>
                        <span className="text-xs text-muted">#{p.number}</span><a className="flex-1 truncate text-brand hover:underline" href={safeHref(p.url)} target="_blank" rel="noreferrer">{p.title}</a>
                        {p.jira_keys.map((k) => <Badge key={k} tone="brand">{k}</Badge>)}<span className="text-xs text-muted">{p.files} files</span></li>))}</ul>
                  </Card>)}
                <Card><CardTitle>Recent commits</CardTitle>
                  <ul className="space-y-2 text-sm">{commits.data?.map((c) => (
                    <li key={c.sha} className="flex gap-3"><code className="text-xs text-muted">{c.sha.slice(0, 8)}</code><span className="flex-1 truncate">{c.message.split("\n")[0]}</span><span className="text-xs text-muted">{c.author} · {timeAgo(c.committed_at)}</span></li>))}</ul></Card>
              </div>
            </div>
          </>)}
        </div>
      </div>
    </div>
  );
}
