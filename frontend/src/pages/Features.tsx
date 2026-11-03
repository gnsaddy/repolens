import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Lock, Share2, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { useConfirm, useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Label, PageHeader, Spinner, Textarea } from "@/components/ui";
import { api, type FeatureDraft, type FeatureDraftView, type Repo } from "@/lib/api";
import { canWrite, useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";
import Markdown from "./wiki/Markdown";

const LEVEL: Record<string, "red" | "amber" | "gray"> = { error: "red", warn: "amber", info: "gray" };
const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/** Ask the model to draft a feature in one repository. The result is text to review: a spec, what depends on the files
 *  that change, a change that was read but not run, tests, a pull request text and the tokens it cost. */
export default function FeaturesPage() {
  const { user } = useAuth();
  const write = canWrite(user);
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [repoId, setRepoId] = useState("");
  const [request, setRequest] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [reusedId, setReusedId] = useState<string | null>(null);  // the page was opened by reusing this draft
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const list = useQuery({ queryKey: ["features"], queryFn: () => api<{ drafts: FeatureDraft[] }>("/features") });
  const ready = (repos.data ?? []).filter((r) => r.source !== "docs" && r.status === "ready");
  const repo = repoId || ready[0]?.id || "";
  const one = useQuery({ queryKey: ["feature", openId], enabled: !!openId, queryFn: () => api<FeatureDraftView>(`/features/${openId}`), retry: false });
  const make = useMutation({
    mutationFn: (fresh: boolean) => api<FeatureDraftView>("/features", { method: "POST", json: { repo_id: repo, request, fresh } }),
    onSuccess: (d) => { setOpenId(d.id); setReusedId(d.reused ? d.id : null); qc.invalidateQueries({ queryKey: ["features"] }); qc.setQueryData(["feature", d.id], d); toast(d.reused ? "Same as your earlier draft, no tokens used" : "Draft ready"); },
    onError: () => qc.invalidateQueries({ queryKey: ["features"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/features/${id}`, { method: "DELETE" }),
    onSuccess: () => { setOpenId(null); qc.invalidateQueries({ queryKey: ["features"] }); toast("Draft deleted"); },
  });
  const share = useMutation({
    mutationFn: (v: { id: string; shared: boolean }) => api<FeatureDraftView>(`/features/${v.id}`, { method: "PATCH", json: { shared: v.shared } }),
    onSuccess: (d) => { qc.setQueryData(["feature", d.id], d); qc.invalidateQueries({ queryKey: ["features"] }); toast(d.shared ? "Shared with the workspace" : "Now private to you"); },
  });
  const ask = async (x: FeatureDraft) => { if (await confirm({ title: "Delete this draft?", message: x.title || "Untitled", confirmLabel: "Delete", danger: true })) remove.mutate(x.id); };
  const d = one.data;
  return (
    <div>
      <PageHeader title="Build a feature" subtitle="The model drafts a feature as a change you review. It is text only: nothing is applied, branched or pushed, and nothing is run." />
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="space-y-4 lg:col-span-2">
          <Card aria-label="New draft">
            <CardTitle>Describe the feature</CardTitle>
            {!write ? <p className="text-sm text-muted">Members can draft features. You can read drafts others shared with the workspace.</p> : (
              <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); make.mutate(false); }}>
                <Label>Repository
                  <select aria-label="Repository" value={repo} onChange={(e) => setRepoId(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-line bg-white px-2 text-sm text-ink">
                    {ready.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Label>
                <Label>What should it do?
                  <Textarea aria-label="What should it do?" rows={6} maxLength={4000} value={request} onChange={(e) => setRequest(e.target.value)}
                    placeholder="Add a percentage discount to the order total, with validation and tests." /></Label>
                <p className="text-xs text-muted">Uses the language model once and spends tokens (usually 5 to 15 thousand). The model sees the code that matches your request, so describe it with the names used in the code. It works only from the indexed commit.</p>
                <ErrorNote error={make.error} />
                <Button type="submit" disabled={make.isPending || !repo || request.trim().length < 10}><Sparkles className="h-4 w-4" />{make.isPending ? "Drafting…" : "Draft the feature"}</Button>
              </form>)}
          </Card>
          <Card aria-label="Drafts">
            <CardTitle>Drafts</CardTitle>
            {list.isLoading ? <Spinner /> : !list.data?.drafts.length ? <p className="text-sm text-muted">No drafts yet.</p> : (
              <ul className="divide-y divide-line text-sm">{list.data.drafts.map((x) => (
                <li key={x.id} className="flex items-start gap-1">
                  <button className={`min-w-0 flex-1 py-2 text-left hover:bg-slate-50 ${openId === x.id ? "font-medium" : ""}`} onClick={() => setOpenId(x.id)}>
                    <div className="truncate">{x.title || "Untitled"}</div>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">{x.repo} · {x.mine ? "you" : x.author_name} · {timeAgo(x.created_at)} · {tokens(x.tokens.input_tokens + x.tokens.output_tokens)} tokens
                      <Badge tone={x.status === "failed" ? "red" : x.ok === false ? "amber" : "green"}>{x.status === "failed" ? "failed" : x.ok === false ? "needs work" : "drafted"}</Badge>
                      <Badge tone={x.shared ? "blue" : "gray"}>{x.shared ? "shared" : "private"}</Badge></div>
                  </button>
                  {(x.mine || user?.role === "admin") && <Button size="sm" variant="ghost" aria-label={`Delete ${x.title || "draft"}`} className="mt-1.5" onClick={() => ask(x)}><Trash2 className="h-4 w-4" /></Button>}
                </li>))}</ul>)}
          </Card>
        </div>

        <div className="space-y-4 lg:col-span-3">
          {!openId ? <Empty>Describe a feature, or open a draft.</Empty> : one.isLoading ? <Spinner /> : !d ? <ErrorNote error={one.error} /> : <Draft d={d} reused={d.id === reusedId} write={write} admin={user?.role === "admin"} again={make.isPending ? undefined : () => make.mutate(true)}
            onShare={(shared) => share.mutate({ id: d.id, shared })} onDelete={() => ask(d)} />}
        </div>
      </div>
    </div>
  );
}

function Draft({ d, reused, write, admin, again, onShare, onDelete }: { d: FeatureDraftView; reused: boolean; write: boolean; admin: boolean; again?: () => void; onShare: (shared: boolean) => void; onDelete: () => void }) {
  const toast = useToast();
  const copy = (text: string, what: string) => navigator.clipboard?.writeText(text).then(() => toast(`${what} copied`), () => toast("Could not copy"));
  const t = d.tokens;
  return (
    <>
      <Card aria-label="Draft summary">
        <CardTitle right={<span className="flex items-center gap-2">
          {d.mine && <Button size="sm" variant="outline" onClick={() => onShare(!d.shared)}>{d.shared ? <><Lock className="h-4 w-4" />Make private</> : <><Share2 className="h-4 w-4" />Share with workspace</>}</Button>}
          {(d.mine || admin) && <Button size="sm" variant="outline" aria-label="Delete draft" onClick={onDelete}><Trash2 className="h-4 w-4" />Delete</Button>}</span>}>{d.title || "Draft"}</CardTitle>
        {reused && (
          <p role="status" className="mb-2 rounded-lg bg-blue-50 p-2 text-xs text-blue-800">This is your earlier draft for the same request on the same code, so no tokens were used.
            {write && again && <Button size="sm" variant="outline" className="ml-2" onClick={again}>Draft again anyway</Button>}</p>)}
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <Badge tone={d.status === "failed" ? "red" : "green"}>{d.status}</Badge>
          <Badge tone={d.shared ? "blue" : "gray"}>{d.shared ? `shared by ${d.mine ? "you" : d.author_name}` : "private to you"}</Badge>
          <span>{d.repo} · written against {d.base_sha.slice(0, 8)} · {d.model || "no model"}</span>
          <span aria-label="Tokens used">{tokens(t.input_tokens)} in, {tokens(t.output_tokens)} out, {t.calls} call{t.calls === 1 ? "" : "s"}{t.cost ? `, about $${t.cost.toFixed(4)}` : ""}</span>
        </div>
        {d.status === "failed" ? <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{d.error}</p> : <Markdown text={d.spec} onOpen={() => {}} />}
        <p className="mt-3 text-xs text-muted">{d.note}</p>
      </Card>
      {d.status === "drafted" && (<>
        <Card aria-label="Impact">
          <CardTitle>What depends on the files this changes</CardTitle>
          {!d.impact.length ? <p className="text-sm text-muted">The change touches no existing file, so nothing depends on it yet.</p> : (
            <ul className="space-y-1.5 text-sm">{d.impact.map((i) => (
              <li key={i.file}><code>{i.file}</code>{" "}
                {i.dependants ? <span>is used by {i.dependants} place{i.dependants === 1 ? "" : "s"} in {i.repos.join(", ")}{i.hidden ? ` and ${i.hidden} you cannot access` : ""}
                  <span className="block text-xs text-muted">{i.examples.join("; ")}</span></span>
                  : <span className="text-muted">has no dependants in other repositories that RepoLens can see</span>}</li>))}</ul>)}
        </Card>
        <Card aria-label="Review">
          <CardTitle right={<Badge tone={d.review.ok ? "green" : "red"}>{d.review.ok ? "no errors" : `${d.review.errors} error${d.review.errors === 1 ? "" : "s"}`}</Badge>}>Review (read, not run)</CardTitle>
          <ul className="space-y-1.5 text-sm">{d.review.findings.map((f, i) => (
            <li key={i} className="flex items-start gap-2"><Badge tone={LEVEL[f.level] ?? "gray"}>{f.level}</Badge><span>{f.file && <code className="mr-1">{f.file}</code>}{f.message}</span></li>))}</ul>
          <p className="mt-2 text-xs text-muted">{d.review.note}</p>
        </Card>
        <Card aria-label="Change">
          <CardTitle right={<Button size="sm" variant="outline" onClick={() => copy(d.diff, "Diff")}><Copy className="h-4 w-4" />Copy diff</Button>}>The change</CardTitle>
          <pre className="max-h-[32rem] overflow-auto rounded-lg bg-slate-50 p-3 text-xs">{d.diff || "No change."}</pre>
          {d.new_files.length > 0 && <p className="mt-2 text-xs text-muted">New files: {d.new_files.map((f) => `${f.path}${f.test ? " (test)" : ""}`).join(", ")}</p>}
        </Card>
        <Card aria-label="Pull request text">
          <CardTitle right={<Button size="sm" variant="outline" onClick={() => copy(`${d.pr_title}\n\n${d.pr_body}`, "Pull request text")}><Copy className="h-4 w-4" />Copy</Button>}>Pull request text</CardTitle>
          <div className="font-medium">{d.pr_title}</div>
          <Markdown text={d.pr_body} onOpen={() => {}} />
        </Card>
      </>)}
    </>
  );
}
