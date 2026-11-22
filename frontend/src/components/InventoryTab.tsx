import { useQuery } from "@tanstack/react-query";
import { Copy, ListChecks } from "@phosphor-icons/react";
import { useState } from "react";
import { useToast } from "@/components/feedback";
import { Button, Card, CardTitle, Empty, ErrorNote, Input, Spinner } from "@/components/ui";
import { api, type Inventory, type InventoryFile, type Repo } from "@/lib/api";
import { cn } from "@/lib/utils";

const KINDS = [
  { key: "envvars", label: "Environment variables", hint: "Names the code reads, settings fields and entries in example env files." },
  { key: "cli", label: "Commands", hint: "Command line commands and options, make targets and package scripts." },
  { key: "tables", label: "Database tables", hint: "Tables from model classes and SQL files, with their first columns." },
] as const;
type Kind = (typeof KINDS)[number]["key"];

const match = (files: InventoryFile[], q: string) => {
  const t = q.trim().toLowerCase();
  if (!t) return files;
  return files
    .map((f) => ({ ...f, items: f.items.filter((i) => `${i.name} ${i.detail} ${f.file}`.toLowerCase().includes(t)) }))
    .filter((f) => f.items.length);
};
const asText = (files: InventoryFile[]) => files.flatMap((f) => f.items.map((i) => `${i.name}${i.detail ? `: ${i.detail}` : ""}\t${f.repo}/${f.file}:${i.line}`)).join("\n");

/** The environment variables, commands and database tables of the repositories, without asking for them in chat. */
export default function InventoryTab({ repos }: { repos: Repo[] }) {
  const toast = useToast();
  const [repoId, setRepoId] = useState("");
  const [kind, setKind] = useState<Kind>("envvars");
  const [q, setQ] = useState("");
  const inv = useQuery({ queryKey: ["inventory", repoId], queryFn: () => api<Inventory>(`/inventory${repoId ? `?repo_id=${repoId}` : ""}`) });
  const data = inv.data;
  const files = data ? match(data[kind].files, q) : [];
  const shown = files.reduce((n, f) => n + f.items.length, 0);
  const none = data && KINDS.every((k) => data[k.key].count === 0);
  return (
    <Card aria-label="Inventory">
      <CardTitle icon={ListChecks}>Inventory</CardTitle>
      <p className="mb-3 caption-5 text-muted">Every environment variable, command and database table found when the repositories were indexed, in one place. Pattern based, so a name built at run time is not seen. Tests, docs and migrations are left out. The same lists come from asking "list all the environment variables" in Ask.</p>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select aria-label="Repository" className="h-9 rounded-lg border border-line bg-white px-2 body-6" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
          <option value="">All repositories</option>
          {repos.filter((r) => r.source !== "docs").map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <Input aria-label="Filter" className="max-w-xs" placeholder="Filter by name, file or detail" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div role="tablist" aria-label="Inventory kinds" className="mb-2 flex gap-1 border-b border-line">
        {KINDS.map((k) => (
          <button key={k.key} role="tab" aria-selected={kind === k.key} onClick={() => setKind(k.key)} className={cn("-mb-px border-b-2 px-3 py-2 body-6", kind === k.key ? "border-brand text-brand" : "border-transparent text-muted hover:text-ink")}>{k.label}{data ? ` ${data[k.key].count}` : ""}</button>))}
      </div>
      {inv.isLoading ? <Spinner /> : !data ? <ErrorNote error={inv.error} /> : none ? (
        <Empty>Nothing is listed yet. Open Repositories and click Re-index on a repository: the lists are written when it is indexed.</Empty>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="caption-5 text-muted">{KINDS.find((k) => k.key === kind)?.hint}</span>
            <span className="caption-5 text-muted">{q ? `${shown} of ${data[kind].count} shown` : `${data[kind].count} in ${data[kind].files.length} files`}</span>
            <Button size="sm" variant="outline" className="ml-auto" disabled={!shown} onClick={() => { void navigator.clipboard?.writeText(asText(files)); toast("Copied the list"); }}><Copy className="mr-1 h-3 w-3" aria-hidden />Copy list</Button>
          </div>
          {!files.length ? <Empty>{q ? "Nothing matches the filter." : "None found of this kind."}</Empty> : files.map((f) => (
            <section key={`${f.repo_id}:${f.file}`} aria-label={`${f.repo} ${f.file}`} className="rounded-lg border border-line">
              <h3 className="border-b border-line px-3 py-2 caption-5 text-muted"><span className="text-ink">{f.repo}</span> · {f.file}</h3>
              <ul className="divide-y divide-line">
                {f.items.map((i) => (
                  <li key={`${i.name}:${i.line}`} className="flex flex-wrap items-baseline gap-x-3 px-3 py-1.5">
                    <code className="body-6">{i.name}</code>
                    {i.detail && <span className="caption-5 text-muted">{i.detail}</span>}
                    <span className="ml-auto caption-5 text-muted">line {i.line}</span>
                  </li>))}
              </ul>
            </section>))}
        </div>
      )}
    </Card>
  );
}
