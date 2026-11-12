import { useQuery } from "@tanstack/react-query";
import { Lightning } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { Badge, Card, CardTitle, StatGroup } from "@/components/ui";
import { api, type AgentSummary as Summary } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

const EVENT_LABEL: Record<string, string> = { ci_failure: "CI failures", pr_review: "Pull request reviews", drift_draft: "Stale page drafts", watch: "Nightly watch" };

/** What the agent has been doing, for admins: on the Dashboard in short, on Insights with the detail. */
export default function AgentSummary({ detailed = false }: { detailed?: boolean }) {
  const { user } = useAuth();
  const q = useQuery({ queryKey: ["agent-summary"], enabled: user?.role === "admin", refetchInterval: 30000, retry: false, queryFn: () => api<Summary>("/automations/summary") });
  const s = q.data;
  if (user?.role !== "admin" || !s || typeof s.rules_on !== "number") return null;  // not an admin, or a server that does not send it yet
  const state = s.paused ? <Badge tone="amber">paused</Badge> : s.rules_on > 0 ? <Badge tone="green">{s.rules_on} rule{s.rules_on === 1 ? "" : "s"} on</Badge> : <Badge tone="gray">off</Badge>;
  const budget = s.token_budget ? ` of ${s.token_budget.toLocaleString()}` : "";
  return (
    <Card className={detailed ? "" : "mb-6"} aria-label="Agent">
      <CardTitle icon={Lightning} right={<span className="flex items-center gap-3">{state}<Link to="/agent" className="caption-5 text-brand hover:underline">Open the agent</Link></span>}>Agent</CardTitle>
      {s.rules_on === 0 && s.runs_24h === 0 ? (
        <p className="body-6 text-muted">It is off. Turn it on for a repository to have failed CI runs investigated and new pull requests reviewed by itself. It only drafts: nothing reaches GitHub unless you approve it.</p>
      ) : (
        <div className="space-y-3">
          <StatGroup items={[
            { label: "Runs, last day", value: s.runs_24h, hint: s.working_now ? `${s.working_now} going on now` : undefined },
            { label: "Failed", value: s.failed_24h, hint: s.failed_24h ? <Link to="/agent" className="text-brand hover:underline">See why</Link> : "none" },
            { label: "Waiting in the Inbox", value: s.waiting, hint: s.waiting ? <Link to="/agent" className="text-brand hover:underline">Read them</Link> : "nothing" },
            { label: "Tokens, last day", value: s.tokens_used.toLocaleString(), hint: s.token_budget ? `of a budget of ${s.token_budget.toLocaleString()}` : "no budget set" },
          ]} />
          {s.last_run && <p className="caption-5 text-muted">Last run: <span className="text-ink">{s.last_run.title || EVENT_LABEL[s.last_run.event]}</span> · {s.last_run.status} · {timeAgo(s.last_run.created_at)}</p>}
          {detailed && (
            <table className="w-full body-6" aria-label="Agent runs by kind">
              <thead><tr className="text-left caption-5 text-muted"><th className="py-1 font-normal">Kind</th><th className="font-normal">Runs, last day</th><th className="font-normal">Failed</th></tr></thead>
              <tbody>{Object.entries(s.by_event).map(([k, v]) => <tr key={k} className="border-t border-line"><td className="py-2">{EVENT_LABEL[k] ?? k}</td><td>{v.runs}</td><td>{v.failed}</td></tr>)}</tbody>
            </table>)}
          {detailed && s.token_budget > 0 && <p className="caption-5 text-muted">Token budget: {s.tokens_used.toLocaleString()}{budget} used in the last day. When it is used, nothing starts by itself until it falls again.</p>}
        </div>)}
    </Card>
  );
}
