import { useQuery } from "@tanstack/react-query";
import { Brain, CheckCircle2, KeyRound, Plug, SearchCheck, ShieldCheck, Sparkles, UserCog } from "lucide-react";
import Logo from "@/components/Logo";
import { ISSUES_URL } from "@/components/ReportIssue";
import { APP_VERSION } from "@/lib/version";
import { PeoplePanel } from "@/pages/People";
import { Link, useSearchParams } from "react-router-dom";
import { ConnectionCard, TINTS } from "@/components/ConnectionCard";
import AccessTokens from "@/components/AccessTokens";
import IntegrationsPanel from "@/components/IntegrationsPanel";
import LanguageModelCard from "@/components/LanguageModelCard";
import QualityPanel from "@/components/QualityPanel";
import SkillsPanel from "@/components/SkillsPanel";
import OutboundCard from "@/components/OutboundCard";
import ModelRoutingCard, { MemoryCard } from "@/components/ModelRoutingCard";
import { Badge, PageHeader } from "@/components/ui";
import { api, type ModelRegistry, type RetrievalStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const TABS = [["integrations", "Integrations", Plug], ["ai", "AI & search", Brain], ["skills", "Skills", Sparkles], ["quality", "Quality", CheckCircle2], ["mcp", "MCP & tokens", KeyRound], ["people", "People", UserCog]] as const;

export default function SettingsPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const asked = sp.get("tab");
  const tab = asked === "ai" ? "ai" : asked === "skills" ? "skills" : asked === "quality" ? "quality" : asked === "mcp" ? "mcp" : asked === "people" && admin ? "people" : "integrations";
  const tabs = TABS.filter(([k]) => k !== "people" || admin);
  const registry = useQuery({ queryKey: ["model-registry"], queryFn: () => api<ModelRegistry>("/settings/models") });
  const managed = !!registry.data?.connections?.some((c) => !c.legacy); // once models are managed below, one card is enough

  return (
    <div>
      <PageHeader title="Settings" subtitle="Connect the tools your team already uses, a language model if you want AI-written answers, and the people who use RepoLens." />
      <div role="tablist" aria-label="Settings sections" className="mb-6 flex gap-1 border-b border-line">
        {tabs.map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setSp(k === "integrations" ? {} : { tab: k })}
            className={cn("-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm", tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink")}><Icon className="h-4 w-4" />{label}</button>))}
      </div>
      <div className={tab === "people" ? "" : "max-w-4xl"}>
        {tab === "people" ? <PeoplePanel /> : tab === "integrations" ? <IntegrationsPanel admin={admin} /> : tab === "quality" ? <QualityPanel canWrite={!!user && user.role !== "viewer"} /> : tab === "skills" ? <SkillsPanel admin={admin} canSubmit={!!user && user.role !== "viewer"} /> : tab === "mcp" ? (user ? <AccessTokens canWrite={user.role !== "viewer"} /> : null) : (
          <div className="space-y-4">
            <OutboundCard />
            {!managed && <LanguageModelCard admin={admin} />}
            <ModelRoutingCard admin={admin} />
            <MemoryCard admin={admin} />
            <RetrievalCard />
          </div>)}
      </div>
      <section aria-label="About RepoLens AI" className="mt-10 flex max-w-4xl flex-wrap items-center gap-4 rounded-xl border border-line p-4">
        <Logo className="h-12 w-12" />
        <div className="min-w-0 flex-1">
          <div className="font-semibold">RepoLens AI <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">{APP_VERSION}</span></div>
          <p className="text-xs text-muted">A living wiki for your code and docs, with evidence-backed investigations. Beta: expect rough edges. Apache License 2.0.</p>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
          <Link className="text-brand hover:underline" to="/about">About</Link>
          <Link className="text-brand hover:underline" to="/security">Security</Link>
          <a className="text-brand hover:underline" href={`${ISSUES_URL}/new/choose`} target="_blank" rel="noopener noreferrer">Report a bug or request a feature</a>
          <a className="text-brand hover:underline" href={ISSUES_URL.replace(/\/issues$/, "")} target="_blank" rel="noopener noreferrer">Source code</a>
        </div>
      </section>
    </div>
  );
}

function RetrievalCard() {
  const { data } = useQuery({ queryKey: ["retrieval-status"], queryFn: () => api<RetrievalStatus>("/settings/retrieval") });
  if (!data) return null;
  return (
    <ConnectionCard icon={SearchCheck} tint="emerald" title="Search quality" description="How results are found and ranked. Set with environment variables on the server (REPOLENS_EMBEDDING_* and REPOLENS_RERANK_*)."
      status={<span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium", TINTS.slate)}><ShieldCheck className="h-3 w-3" />{data.embedding.offline ? "runs offline" : "uses a hosted embedder"}</span>}>
      {data.embedding_state?.stale && <p role="alert" className="mb-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{data.embedding_state.message} An admin can run <code>python -m repolens.devtools.reembed</code> on the server.</p>}
      <dl className="grid gap-4 text-sm sm:grid-cols-2">
        <div><dt className="text-xs text-muted">Embeddings</dt><dd className="font-medium">{data.embedding.model} <Badge tone={data.embedding.offline ? "gray" : "brand"}>{data.embedding.offline ? "offline" : data.embedding.provider}</Badge></dd>
          <dd className="text-xs text-muted">{data.embedding.dimensions} dimensions{data.embedding.offline ? " · works without internet; an embedding model finds re-worded questions better (see evals/README)" : ""}</dd></div>
        <div><dt className="text-xs text-muted">Reranker</dt><dd className="font-medium">{data.rerank.provider === "none" ? "Off" : data.rerank.model} {!data.rerank.configured && <Badge tone="amber">no endpoint set</Badge>}</dd>
          <dd className="text-xs text-muted">{data.rerank.provider === "none" ? "Enable with REPOLENS_RERANK_PROVIDER" : `re-scores the top ${data.rerank.depth} results of every search`}</dd></div>
      </dl>
    </ConnectionCard>
  );
}
