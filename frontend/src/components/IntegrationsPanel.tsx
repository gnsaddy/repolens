import { useQuery } from "@tanstack/react-query";
import { Buildings as Building2, ArrowSquareOut as ExternalLink, FileText, GitFork, GitMerge, Books as Library, ChatCircle as MessageSquare, Notebook as NotebookText, Plugs as Plug, Ticket } from "@phosphor-icons/react";
import type { Icon as IconType } from "@phosphor-icons/react";
import { useState, type ComponentType } from "react";
import { Link } from "react-router-dom";
import { TINTS, StatusPill } from "@/components/ConnectionCard";
import { BitbucketForm, useBitbucket } from "@/components/connections/bitbucket";
import { ConfluenceForm, useConfluence } from "@/components/connections/confluence";
import { GithubForm, useGithub } from "@/components/connections/github";
import { JiraForm, useJira } from "@/components/connections/jira";
import type { Integration } from "@/components/connections/types";
import { useConfirm } from "@/components/feedback";
import Modal from "@/components/Modal";
import { Button, Card, Empty } from "@/components/ui";
import { api, type WikiSourceStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Def {
  id: string; name: string; description: string; icon: IconType; tint: keyof typeof TINTS;
  state?: Integration; Form?: ComponentType<{ onDone: () => void }>; formTitle?: string;
  /** Sources that are set up per collection in the wiki rather than once per workspace. */
  wiki?: { kind: string; count: number };
}

function IconTile({ icon: Icon, tint }: { icon: IconType; tint: keyof typeof TINTS }) {
  return <span className={cn("grid h-11 w-11 shrink-0 place-items-center rounded-xl ring-1", TINTS[tint])}><Icon className="h-5 w-5" /></span>;
}

/** Connected integrations on top, everything else below with a Connect button that opens a card for the credentials. */
export default function IntegrationsPanel({ admin }: { admin: boolean }) {
  const ask = useConfirm();
  const github = useGithub(), bitbucket = useBitbucket(), jira = useJira(), confluence = useConfluence();
  const sources = useQuery({ queryKey: ["wiki-sources"], queryFn: () => api<WikiSourceStatus[]>("/wiki/sources") });
  const count = (k: string) => (Array.isArray(sources.data) ? sources.data : []).filter((s) => s.kind === k).length;
  const [open, setOpen] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, { ok: boolean; text: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const defs: Def[] = [
    { id: "github", name: "GitHub", description: "Browse repositories and pull requests, import PRs, clone private repositories.", icon: GitFork, tint: "slate", state: github, Form: GithubForm, formTitle: "Connect GitHub" },
    { id: "bitbucket", name: "Bitbucket", description: "Browse repositories and pull requests on Cloud, Server or Data Center.", icon: GitMerge, tint: "blue", state: bitbucket, Form: BitbucketForm, formTitle: "Connect Bitbucket" },
    { id: "jira", name: "Jira", description: "Investigate bug tickets, post approved reports, and search past incidents.", icon: Ticket, tint: "blue", state: jira, Form: JiraForm, formTitle: "Connect Jira" },
    { id: "confluence", name: "Confluence", description: "Import spaces and pages into the DeepMind Wiki and keep them in sync.", icon: Library, tint: "indigo", state: confluence, Form: ConfluenceForm, formTitle: "Connect Confluence" },
    { id: "slack", name: "Slack", description: "Turn helpful threads and busy days into searchable notes.", icon: MessageSquare, tint: "violet", wiki: { kind: "slack", count: count("slack") } },
    { id: "notion", name: "Notion", description: "Import pages shared with an integration token.", icon: NotebookText, tint: "slate", wiki: { kind: "notion", count: count("notion") } },
    { id: "gdocs", name: "Google Docs", description: "Import Docs and Markdown from a Drive folder via a service account.", icon: FileText, tint: "emerald", wiki: { kind: "gdocs", count: count("gdocs") } },
    { id: "sharepoint", name: "SharePoint", description: "Import .docx, .md and .html from a document library.", icon: Building2, tint: "amber", wiki: { kind: "sharepoint", count: count("sharepoint") } },
  ];
  const isOn = (d: Def) => (d.state ? d.state.connected : (d.wiki?.count ?? 0) > 0);
  const connected = defs.filter(isOn);
  const available = defs.filter((d) => !isOn(d));
  const current = defs.find((d) => d.id === open);

  const runTest = async (d: Def) => {
    setBusy(d.id);
    try { const r = await d.state!.test!(); setNotes((n) => ({ ...n, [d.id]: typeof r === "string" ? { ok: true, text: r } : { ok: false, text: r.error } })); }
    catch (e) { setNotes((n) => ({ ...n, [d.id]: { ok: false, text: (e as Error).message } })); }
    finally { setBusy(null); }
  };
  const disconnect = async (d: Def) => {
    if (!(await ask({ title: `Disconnect ${d.name}?`, message: d.state!.disconnectMessage, confirmLabel: "Disconnect", danger: true }))) return;
    setBusy(d.id);
    try { await d.state!.disconnect(); setNotes((n) => Object.fromEntries(Object.entries(n).filter(([k]) => k !== d.id))); } finally { setBusy(null); }
  };

  return (
    <div className="space-y-8">
      <section aria-labelledby="connected-h">
        <div className="mb-3 flex items-center gap-2"><h2 id="connected-h" className="title-2 tracking-tight">Connected</h2><span className="rounded-full bg-emerald-50 px-2 py-0.5 caption-4 text-emerald-700">{connected.length}</span></div>
        {!connected.length ? <Empty>Nothing connected yet. Pick an integration below to get started.</Empty> : (
          <Card className="divide-y divide-line p-0">
            <ul aria-label="Connected integrations">{connected.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-4 p-4">
                <IconTile icon={d.icon} tint={d.tint} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2"><span className="font-semibold">{d.name}</span>{d.wiki && <StatusPill on onLabel={`${d.wiki.count} source${d.wiki.count === 1 ? "" : "s"}`} />}{d.state?.account && <span className="truncate rounded-full bg-slate-100 px-3 py-1 caption-4 text-slate-700" title="The account this credential belongs to">{d.state.account}</span>}</div>
                  <p className="truncate body-6 text-muted">{d.wiki ? "Set up per collection in the wiki." : d.state?.detail}</p>
                  {notes[d.id] && <p role="status" className={cn("body-6", notes[d.id].ok ? "text-emerald-700" : "text-red-600")}>{notes[d.id].text}</p>}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {d.wiki ? <Link to={`/wiki?tab=sources`} className="inline-flex h-8 items-center gap-1 rounded-lg border border-line px-3 body-6 hover:bg-slate-50">Manage<ExternalLink className="h-3.5 w-3.5" /></Link>
                    : admin && (<>
                      {d.state?.test && <Button size="sm" variant="outline" disabled={busy === d.id} onClick={() => runTest(d)} aria-label={`Test ${d.name}`}>Test</Button>}
                      <Button size="sm" variant="outline" onClick={() => setOpen(d.id)} aria-label={`Configure ${d.name}`}>Configure</Button>
                      <Button size="sm" variant="danger-outline" disabled={busy === d.id} onClick={() => disconnect(d)} aria-label={`Disconnect ${d.name}`}>Disconnect</Button></>)}
                </div>
              </li>))}</ul>
          </Card>)}
      </section>

      <section aria-labelledby="available-h">
        <h2 id="available-h" className="mb-3 title-2 tracking-tight">Available integrations</h2>
        <Card className="divide-y divide-line p-0">
          <ul aria-label="Available integrations">{available.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-4 p-4">
              <IconTile icon={d.icon} tint={d.tint} />
              <div className="min-w-0 flex-1"><div className="font-semibold">{d.name}</div><p className="body-6 text-muted">{d.description}</p></div>
              {d.wiki ? <Link to={`/wiki?tab=sources&connector=${d.wiki.kind}`} className="inline-flex h-9 items-center rounded-lg bg-brand px-4 body-5 text-white hover:bg-brand-600" aria-label={`Connect ${d.name}`}>Connect</Link>
                : admin ? <Button onClick={() => setOpen(d.id)} aria-label={`Connect ${d.name}`}>Connect</Button> : <span className="caption-5 text-muted">Admins only</span>}
            </li>))}
            {!available.length && <li className="p-6 text-center body-6 text-muted"><Plug className="mx-auto mb-1 h-5 w-5" />Everything is connected.</li>}</ul>
        </Card>
        <p className="mt-2 caption-5 text-muted">Slack, Notion, Google Docs and SharePoint are set up per collection under Wiki → Sources, so one workspace can have several.</p>
      </section>

      {current?.Form && (
        <Modal title={current.formTitle ?? `Connect ${current.name}`} subtitle={current.description} icon={<IconTile icon={current.icon} tint={current.tint} />} onClose={() => setOpen(null)}>
          <current.Form onDone={() => setOpen(null)} />
        </Modal>)}
    </div>
  );
}
