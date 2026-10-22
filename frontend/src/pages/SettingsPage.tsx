import { useQuery } from "@tanstack/react-query";
import { Brain, Plug, SearchCheck, ShieldCheck } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { ConnectionCard, TINTS } from "@/components/ConnectionCard";
import IntegrationsPanel from "@/components/IntegrationsPanel";
import LanguageModelCard from "@/components/LanguageModelCard";
import ModelRoutingCard, { MemoryCard } from "@/components/ModelRoutingCard";
import { Badge, PageHeader } from "@/components/ui";
import { api, type RetrievalStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const TABS = [["integrations", "Integrations", Plug], ["ai", "AI & search", Brain]] as const;

export default function SettingsPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "ai" ? "ai" : "integrations";

  return (
    <div>
      <PageHeader title="Settings" subtitle="Connect the tools your team already uses, and a language model if you want AI-written answers." />
      <div role="tablist" aria-label="Settings sections" className="mb-6 flex gap-1 border-b border-line">
        {TABS.map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setSp(k === "integrations" ? {} : { tab: k })}
            className={cn("-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm", tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink")}><Icon className="h-4 w-4" />{label}</button>))}
      </div>
      <div className="max-w-4xl">
        {tab === "integrations" ? <IntegrationsPanel admin={admin} /> : (
          <div className="space-y-4">
            <LanguageModelCard admin={admin} />
            <ModelRoutingCard admin={admin} />
            <MemoryCard admin={admin} />
            <RetrievalCard />
          </div>)}
      </div>
    </div>
  );
}

function RetrievalCard() {
  const { data } = useQuery({ queryKey: ["retrieval-status"], queryFn: () => api<RetrievalStatus>("/settings/retrieval") });
  if (!data) return null;
  return (
    <ConnectionCard icon={SearchCheck} tint="emerald" title="Search quality" description="How results are found and ranked. Set with environment variables on the server (REPOLENS_EMBEDDING_* and REPOLENS_RERANK_*)."
      status={<span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium", TINTS.slate)}><ShieldCheck className="h-3 w-3" />{data.embedding.offline ? "runs offline" : "uses a hosted embedder"}</span>}>
      <dl className="grid gap-4 text-sm sm:grid-cols-2">
        <div><dt className="text-xs text-muted">Embeddings</dt><dd className="font-medium">{data.embedding.model} <Badge tone={data.embedding.offline ? "gray" : "brand"}>{data.embedding.offline ? "offline" : data.embedding.provider}</Badge></dd>
          <dd className="text-xs text-muted">{data.embedding.dimensions} dimensions{data.embedding.offline ? " · works without internet, weaker on natural language" : ""}</dd></div>
        <div><dt className="text-xs text-muted">Reranker</dt><dd className="font-medium">{data.rerank.provider === "none" ? "Off" : data.rerank.model} {!data.rerank.configured && <Badge tone="amber">no endpoint set</Badge>}</dd>
          <dd className="text-xs text-muted">{data.rerank.provider === "none" ? "Enable with REPOLENS_RERANK_PROVIDER" : `re-scores the top ${data.rerank.depth} results of every search`}</dd></div>
      </dl>
    </ConnectionCard>
  );
}
