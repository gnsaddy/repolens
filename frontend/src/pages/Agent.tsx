import { FileMagnifyingGlass, GitPullRequest, Lightning, Moon, ShieldCheck, UserCheck } from "@phosphor-icons/react";
import AgentInbox from "@/components/AgentInbox";
import AutomationPanel from "@/components/AutomationPanel";
import { Card, CardTitle, PageHeader } from "@/components/ui";

const FLOWS = [
  {
    icon: Lightning, title: "A CI run fails", steps: [
      "GitHub sends a signed message that a workflow run failed. RepoLens ignores any that is not signed.",
      "It reads the end of up to three failed job logs and masks keys and tokens.",
      "It starts one investigation for each failing test or error, up to three for one run.",
      "The report waits in Investigations, marked automatic.",
    ],
  },
  {
    icon: GitPullRequest, title: "A pull request is opened", steps: [
      "GitHub sends a signed message that a pull request was opened, reopened or taken out of draft.",
      "It reads the changed files as the pull request has them, up to ten, and the lines that changed.",
      "The Developer and the Security auditor each review them. A finding is kept only if it quotes real code.",
      "The reviews wait in the Inbox below as a draft comment. It is posted on the pull request only if an admin approves it.",
    ],
  },
  {
    icon: FileMagnifyingGlass, title: "A page goes stale", steps: [
      "After an index, a wiki page that describes code that changed has an open drift alert.",
      "RepoLens drafts an update for it, as an AI model or from a template if none is connected.",
      "The draft waits in the wiki proposals and the Inbox. Nothing is written to the page until you approve it.",
    ],
  },
  {
    icon: Moon, title: "Every night", steps: [
      "A plain list, with no model, of pages that have described changed code for more than two weeks.",
      "At most one list a week for each repository, in the Inbox.",
    ],
  },
] as const;

/** The agent that starts investigations by itself: how it works, its switches, and what it did. */
export default function AgentPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Agent" subtitle="Works by itself when something happens on GitHub: investigates a failed CI run and reviews a new pull request." />
      <Card aria-label="How the agent works">
        <CardTitle icon={ShieldCheck}>How it works</CardTitle>
        <div className="grid gap-6 md:grid-cols-2">
          {FLOWS.map(({ icon: Icon, title, steps }) => (
            <div key={title} className="space-y-2">
              <h3 className="flex items-center gap-2 title-4"><Icon className="h-5 w-5 text-brand" aria-hidden />{title}</h3>
              <ol className="list-decimal space-y-1 pl-6 caption-5 text-muted">{steps.map((s) => <li key={s}>{s}</li>)}</ol>
            </div>))}
        </div>
        <p className="mt-4 flex items-start gap-2 caption-5 text-muted"><UserCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />You decide. It only drafts: nothing reaches GitHub unless an admin posts a drafted comment from the Inbox, and nothing is merged. It runs as the admin who switches it on, with that person's access, and stops if they are no longer an admin. A daily limit and an optional token budget bound the model use, you can pause it or cancel a run, every run keeps the reasons it started, and every start is in the audit log.</p>
      </Card>
      <AgentInbox />
      <AutomationPanel />
    </div>
  );
}
