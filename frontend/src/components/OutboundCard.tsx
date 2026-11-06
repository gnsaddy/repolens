import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, ShieldCheck, ShieldQuestion } from "lucide-react";
import { ConnectionCard, StatusPill } from "@/components/ConnectionCard";
import { Badge } from "@/components/ui";
import { api, type Outbound } from "@/lib/api";

/** Every place this server sends data, so nothing leaves without somebody being able to see it. */
export default function OutboundCard() {
  const { data } = useQuery({ queryKey: ["outbound"], queryFn: () => api<Outbound>("/settings/outbound") });
  if (!data?.items) return null;
  const clean = data.external === 0;
  return (
    <ConnectionCard icon={clean ? ShieldCheck : ShieldQuestion} tint={clean ? "emerald" : "amber"} title="What leaves this server"
      description="Everything RepoLens sends to another service, from what is connected right now. Connections are made by admins and can be removed where shown."
      status={<StatusPill on={clean} onLabel="Nothing leaves" offLabel={`${data.external} outbound`} />}>
      {!data.items.length ? <p className="body-6 text-muted">No model, embedding service or connector is connected. Search, the graph, the wiki and the answer memory all run on this server.</p> : (
        <ul aria-label="Outbound connections" className="divide-y divide-line">{data.items.map((i) => (
          <li key={i.id} className="py-3 body-6">
            <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{i.name}</span>
              {i.host && <code className="rounded-md bg-slate-100 px-1.5 py-0.5 caption-5">{i.host}</code>}
              <Badge tone={i.stays_on_machine ? "green" : "amber"}>{i.stays_on_machine ? "stays on this machine" : "leaves this server"}</Badge></div>
            <ul className="mt-1 list-disc pl-5 caption-5 text-muted">{i.sends.map((s) => <li key={s}>{s}</li>)}</ul>
            {i.note && <p className="mt-1 caption-5 text-muted">{i.note}</p>}
            <p className="mt-1 caption-5 text-muted">Manage: {i.where}</p>
          </li>))}</ul>)}
      <ul className="mt-3 space-y-1 border-t border-line pt-3 caption-5 text-muted">{data.local_only.map((t) => <li key={t} className="flex items-start gap-1.5"><Check className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600" aria-hidden />{t}</li>)}</ul>
      <p className="mt-2 caption-5"><Link className="text-brand hover:underline" to="/profile">Your own sessions and sign-in security</Link></p>
    </ConnectionCard>
  );
}
