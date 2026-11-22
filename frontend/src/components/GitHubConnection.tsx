import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, PlugsConnected } from "@phosphor-icons/react";
import { useToast } from "@/components/feedback";
import { Badge, Button, Card, CardTitle, Empty, ErrorNote, Spinner } from "@/components/ui";
import { api, type AgentWebhook, type WebhookDelivery } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

const TONE = { ok: "green", ignored: "gray", rejected: "red", error: "red" } as const;
const LOCAL = /^(localhost|127\.|\[?::1\]?$|0\.0\.0\.0)/;

function Row({ d }: { d: WebhookDelivery }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
      <Badge tone={TONE[d.outcome]}>{d.outcome}</Badge>
      <span className="body-6">{d.event === "selftest" ? "self test" : d.event}{d.action ? ` · ${d.action}` : ""}</span>
      {d.repo && <span className="caption-5 text-muted">{d.repo}</span>}
      <span className="caption-5 text-muted">{d.note}</span>
      <span className="ml-auto caption-5 text-muted">{timeAgo(d.at)}</span>
    </li>
  );
}

/** Is GitHub connected to this server: what to set on GitHub, a check of the receiving side, and what GitHub sent. */
export default function GitHubConnection() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["agent-webhook"], queryFn: () => api<AgentWebhook>("/automations/webhook"), refetchInterval: 15000 });
  const test = useMutation({
    mutationFn: () => api<AgentWebhook>("/automations/webhook/test", { method: "POST" }),
    onSuccess: (data) => { qc.setQueryData(["agent-webhook"], data); toast("The receiver works. Now send a real delivery from GitHub."); },
  });
  const w = q.data;
  const url = w ? `${window.location.origin}${w.path}` : "";
  const local = LOCAL.test(window.location.hostname);
  const state = !w ? null : w.last_delivery_at ? { tone: "green", text: `GitHub reached RepoLens ${timeAgo(w.last_delivery_at)}` } : !w.secret_set ? { tone: "red", text: "No webhook secret is set on the server" } : { tone: "amber", text: "Waiting for the first delivery from GitHub" };
  return (
    <Card aria-label="GitHub connection">
      <CardTitle icon={PlugsConnected} right={state && <Badge tone={state.tone as "green" | "red" | "amber"}>{state.text}</Badge>}>GitHub connection</CardTitle>
      {q.isLoading ? <Spinner /> : !w ? <ErrorNote error={q.error} /> : (
        <div className="space-y-4">
          <p className="caption-5 text-muted">The agent starts when GitHub sends a signed message to RepoLens. In the repository on GitHub open Settings, Webhooks, Add webhook and fill in:</p>
          <ol className="list-decimal space-y-1 pl-6 caption-5 text-muted">
            <li>Payload URL: <code className="rounded bg-slate-50 px-1 text-ink">{url}</code> <button type="button" className="inline-flex items-center gap-1 text-brand hover:underline" onClick={() => { void navigator.clipboard?.writeText(url); toast("Copied"); }}><Copy className="h-3 w-3" aria-hidden />Copy</button></li>
            <li>Content type: <code className="rounded bg-slate-50 px-1 text-ink">application/json</code></li>
            <li>Secret: the value of <code className="rounded bg-slate-50 px-1 text-ink">REPOLENS_GITHUB_WEBHOOK_SECRET</code> on the server{w.secret_set ? " (it is set)" : " (it is not set yet: set it and restart the server)"}.</li>
            <li>Events: choose "Let me select individual events" and tick {w.events.map((e) => e.event).join(", ")}.</li>
          </ol>
          <ul className="space-y-1 caption-5 text-muted">{w.events.map((e) => <li key={e.event}><span className="text-ink">{e.event}</span>: {e.for}.</li>)}</ul>
          {local && <p className="rounded-lg bg-amber-50 p-3 caption-5 text-ink">This page is on {window.location.hostname}, which GitHub cannot reach. Use the address the server has on the internet, or a tunnel to this machine, as the Payload URL.</p>}
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" variant="outline" disabled={test.isPending || !w.secret_set} onClick={() => test.mutate()}>Check the receiver</Button>
            <span className="caption-5 text-muted">Signs a test message with the server's secret and runs it through the same path. It cannot show that GitHub reaches you: only a delivery below can.</span>
          </div>
          {test.error ? <ErrorNote error={test.error} /> : null}
          {w.deliveries.length ? <ul aria-label="Deliveries" className="divide-y divide-line">{w.deliveries.map((d) => <Row key={d.id} d={d} />)}</ul> : <Empty>Nothing has arrived yet. After the webhook is added, GitHub sends a ping at once.</Empty>}
        </div>
      )}
    </Card>
  );
}
