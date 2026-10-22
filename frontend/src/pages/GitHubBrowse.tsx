import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleDot, ExternalLink, GitBranch, GitPullRequest, Lock, Plus, Star } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Input, PageHeader, Spinner } from "@/components/ui";
import { api, type GhBranch, type GhIssue, type GhPull, type GhRepo, type GhRepoPage, type GithubConn, type Repo } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { cn, timeAgo } from "@/lib/utils";

const PAGE = 25;
const STATE_TONE: Record<string, "green" | "blue" | "gray" | "brand"> = { open: "green", merged: "brand", closed: "gray" };

/** Browse what the connected GitHub token can see (repositories, pull requests, issues, branches) and add repositories. */
export default function GitHubBrowse() {
  const { user } = useAuth();
  const conn = useQuery({ queryKey: ["github-conn"], queryFn: () => api<GithubConn>("/github/connection") });
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { const t = setTimeout(() => { setDebounced(q.trim()); setPage(1); }, 250); return () => clearTimeout(t); }, [q]);
  const repos = useQuery({ queryKey: ["gh-repos", debounced, page], enabled: !!conn.data?.connected, retry: false,
    queryFn: () => api<GhRepoPage>(`/github/repos?q=${encodeURIComponent(debounced)}&page=${page}&per_page=${PAGE}`) });
  const current = repos.data?.items.find((r) => r.full_name === sel) ?? null;

  if (conn.isLoading) return <Spinner />;
  if (!conn.data?.connected) {
    return (
      <div><PageHeader title="GitHub" subtitle="Browse repositories, pull requests and issues your token can see." />
        <Empty>GitHub is not connected. Add a personal access token under <Link className="text-brand hover:underline" to="/settings">Settings → GitHub</Link>.</Empty></div>);
  }
  const totalPages = Math.max(1, Math.ceil((repos.data?.total ?? 0) / PAGE));
  return (
    <div>
      <PageHeader title="GitHub" subtitle={`Connected${conn.data.login ? ` as @${conn.data.login}` : ""} · ${conn.data.api_base}`} />
      <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        <Card className="p-3">
          <Input aria-label="Search repositories" placeholder="Search your repositories…" value={q} onChange={(e) => setQ(e.target.value)} className="mb-3" />
          <ErrorNote error={repos.error} />
          {repos.isLoading ? <Spinner /> : !repos.data?.items.length ? <Empty>{debounced ? "No repository matches." : "This token can not see any repositories. Check its repository access."}</Empty> : (
            <ul className="max-h-[34rem] space-y-1 overflow-auto" aria-label="Repositories">{repos.data.items.map((r) => (
              <li key={r.full_name}><button onClick={() => setSel(r.full_name)} aria-current={sel === r.full_name} className={cn("w-full rounded-md px-2 py-2 text-left hover:bg-slate-50", sel === r.full_name && "bg-indigo-50")}>
                <div className="flex items-center gap-1.5 text-sm font-medium">{r.private && <Lock className="h-3 w-3 shrink-0 text-amber-600" aria-label="private" />}<span className="truncate">{r.full_name}</span>
                  {r.added_repo_id && <Badge tone="green">added</Badge>}</div>
                <div className="truncate text-xs text-muted">{r.description || "No description"}</div>
                <div className="mt-0.5 flex gap-3 text-xs text-muted">{r.language && <span>{r.language}</span>}<span className="flex items-center gap-0.5"><Star className="h-3 w-3" />{r.stars}</span><span>pushed {timeAgo(r.updated_at)}</span></div></button></li>))}</ul>)}
          {!!repos.data && repos.data.total > PAGE && (
            <div className="mt-2 flex items-center justify-between text-xs text-muted">
              <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <span>Page {page} of {totalPages} · {repos.data.total} repositories</span>
              <Button size="sm" variant="ghost" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button></div>)}
          {repos.data?.truncated && <p className="mt-2 text-xs text-amber-700">Showing the 500 most recently pushed repositories. Search to narrow down.</p>}
        </Card>
        <div className="min-w-0">{current ? <RepoDetail key={current.full_name} repo={current} writable={canWrite(user)} /> : <Empty>Select a repository to see its pull requests, issues and branches.</Empty>}</div>
      </div>
    </div>
  );
}

function RepoDetail({ repo, writable }: { repo: GhRepo; writable: boolean }) {
  const [tab, setTab] = useState<"pulls" | "issues" | "branches">("pulls");
  const [state, setState] = useState("open");
  const base = `/github/repos/${repo.owner}/${repo.name}`;
  const pulls = useQuery({ queryKey: ["gh-pulls", repo.full_name, state], enabled: tab === "pulls", retry: false, queryFn: () => api<GhPull[]>(`${base}/pulls?state=${state}`) });
  const issues = useQuery({ queryKey: ["gh-issues", repo.full_name, state], enabled: tab === "issues", retry: false, queryFn: () => api<GhIssue[]>(`${base}/issues?state=${state}`) });
  const branches = useQuery({ queryKey: ["gh-branches", repo.full_name], enabled: tab === "branches", retry: false, queryFn: () => api<GhBranch[]>(`${base}/branches`) });
  return (
    <Card>
      <CardTitle right={<a className="inline-flex items-center gap-1 text-xs text-brand hover:underline" href={repo.html_url} target="_blank" rel="noopener noreferrer">Open on GitHub<ExternalLink className="h-3 w-3" /></a>}>
        <span className="flex items-center gap-2">{repo.full_name}{repo.private && <Badge tone="amber">private</Badge>}{repo.fork && <Badge>fork</Badge>}{repo.archived && <Badge>archived</Badge>}</span></CardTitle>
      <p className="text-sm text-muted">{repo.description || "No description"}</p>
      <p className="mt-1 text-xs text-muted">default branch {repo.default_branch} · {repo.language || "unknown language"} · {repo.open_issues} open issues and PRs</p>
      <div className="mt-3"><AddRepo repo={repo} writable={writable} /></div>
      <div role="tablist" className="mt-5 flex gap-1 border-b border-line">
        {([["pulls", "Pull requests", GitPullRequest], ["issues", "Issues", CircleDot], ["branches", "Branches", GitBranch]] as const).map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm", tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink")}><Icon className="h-4 w-4" />{label}</button>))}
      </div>
      {tab !== "branches" && (
        <div className="mt-3 flex gap-1" role="group" aria-label="State">{["open", "closed", "all"].map((s) => (
          <button key={s} aria-pressed={state === s} onClick={() => setState(s)} className={cn("rounded-full border px-3 py-0.5 text-xs", state === s ? "border-brand bg-indigo-50 text-brand" : "border-line text-muted")}>{s}</button>))}</div>)}
      <div className="mt-3">
        {tab === "pulls" && (pulls.isLoading ? <Spinner /> : pulls.error ? <ErrorNote error={pulls.error} /> : !pulls.data?.length ? <Empty>No {state === "all" ? "" : state} pull requests.</Empty> : (
          <ul className="divide-y divide-line">{pulls.data.map((p) => (
            <li key={p.number} className="flex items-start gap-2 py-2 text-sm"><Badge tone={STATE_TONE[p.state] ?? "gray"}>{p.draft ? "draft" : p.state}</Badge>
              <div className="min-w-0 flex-1"><a className="font-medium text-brand hover:underline" href={p.url} target="_blank" rel="noopener noreferrer">#{p.number} {p.title}</a>
                <div className="text-xs text-muted">{p.author} · {p.head} → {p.base} · updated {timeAgo(p.updated_at)}</div></div>
              {p.labels.slice(0, 3).map((l) => <Badge key={l} tone="blue">{l}</Badge>)}</li>))}</ul>))}
        {tab === "issues" && (issues.isLoading ? <Spinner /> : issues.error ? <ErrorNote error={issues.error} /> : !issues.data?.length ? <Empty>No {state === "all" ? "" : state} issues.</Empty> : (
          <ul className="divide-y divide-line">{issues.data.map((i) => (
            <li key={i.number} className="flex items-start gap-2 py-2 text-sm"><Badge tone={STATE_TONE[i.state] ?? "gray"}>{i.state}</Badge>
              <div className="min-w-0 flex-1"><a className="font-medium text-brand hover:underline" href={i.url} target="_blank" rel="noopener noreferrer">#{i.number} {i.title}</a>
                <div className="text-xs text-muted">{i.author} · {i.comments} comments · updated {timeAgo(i.updated_at)}</div></div>
              {i.labels.slice(0, 3).map((l) => <Badge key={l} tone="blue">{l}</Badge>)}</li>))}</ul>))}
        {tab === "branches" && (branches.isLoading ? <Spinner /> : branches.error ? <ErrorNote error={branches.error} /> : (
          <ul className="grid gap-1 sm:grid-cols-2">{branches.data?.map((b) => <li key={b.name} className="flex items-center gap-2 rounded border border-line px-2 py-1 text-sm"><GitBranch className="h-3.5 w-3.5 text-muted" />{b.name}{b.protected && <Badge tone="amber">protected</Badge>}{b.name === repo.default_branch && <Badge>default</Badge>}</li>)}</ul>))}
      </div>
    </Card>
  );
}

/** Add this repository to RepoLens: choose which branches to index, then it is cloned and indexed. */
function AddRepo({ repo, writable }: { repo: GhRepo; writable: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([repo.default_branch]);
  const branches = useQuery({ queryKey: ["gh-branches", repo.full_name], enabled: open, retry: false, queryFn: () => api<GhBranch[]>(`/github/repos/${repo.owner}/${repo.name}/branches`) });
  const add = useMutation({
    mutationFn: () => api<Repo>("/repositories", { method: "POST", json: { name: repo.name, source: "git", url: repo.clone_url, branches: picked } }),
    onSuccess: () => { toast(`Added ${repo.full_name}. Indexing in the background.`); setOpen(false); qc.invalidateQueries({ queryKey: ["gh-repos"] }); qc.invalidateQueries({ queryKey: ["repos"] }); },
  });
  if (repo.added_repo_id) return <p className="text-sm text-emerald-700">Already in RepoLens. <Link className="text-brand hover:underline" to="/repositories">View under Repositories</Link></p>;
  if (!writable) return <p className="text-xs text-muted">Ask a member or admin to add this repository.</p>;
  if (!open) return <Button size="sm" onClick={() => setOpen(true)}><Plus className="h-4 w-4" />Add to RepoLens</Button>;
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="mb-2 text-xs font-medium text-muted">Branches to index (each is indexed separately, max 8)</div>
      {branches.isLoading ? <Spinner /> : (
        <div className="flex max-h-40 flex-wrap gap-x-4 gap-y-1 overflow-auto">{(branches.data ?? [{ name: repo.default_branch, protected: false }]).map((b) => (
          <label key={b.name} className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={picked.includes(b.name)} onChange={() => setPicked((p) => (p.includes(b.name) ? p.filter((x) => x !== b.name) : [...p, b.name]))} />{b.name}</label>))}</div>)}
      <ErrorNote error={add.error} />
      <div className="mt-3 flex gap-2"><Button size="sm" disabled={!picked.length || add.isPending} onClick={() => add.mutate()}>Clone and index</Button><Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button></div>
    </div>
  );
}
