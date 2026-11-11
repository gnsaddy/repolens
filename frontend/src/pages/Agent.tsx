import { ArrowRight, BookOpenText, FileMagnifyingGlass, Lightning, ShieldCheck, UserCheck } from "@phosphor-icons/react";
import AutomationPanel from "@/components/AutomationPanel";
import { Card, CardTitle, PageHeader } from "@/components/ui";

const STEPS = [
  { icon: Lightning, title: "A CI run fails", text: "GitHub tells RepoLens that a workflow run finished with a failure. The message is signed, and RepoLens ignores any that is not." },
  { icon: FileMagnifyingGlass, title: "It reads the failed jobs", text: "Up to three failed jobs. RepoLens reads the end of each log, where the failure is, and masks keys and tokens before anything is stored." },
  { icon: BookOpenText, title: "It starts investigations", text: "One for each failing test or error, up to three for one run. Each is the same investigation you would start by pasting the log, marked automatic." },
  { icon: UserCheck, title: "You decide", text: "The root cause report waits in Investigations. Nothing is posted, commented or changed on GitHub, and nothing is merged." },
] as const;

/** The agent that starts investigations by itself: how it works, its switches, and what it did. */
export default function AgentPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Agent" subtitle="Investigates a failed CI run by itself, so the report is waiting when you open it." />
      <Card aria-label="How the agent works">
        <CardTitle icon={ShieldCheck}>How it works</CardTitle>
        <ol className="grid gap-6 md:grid-cols-4">
          {STEPS.map(({ icon: Icon, title, text }, i) => (
            <li key={title} className="space-y-2">
              <div className="flex items-center gap-2 text-brand"><Icon className="h-5 w-5" aria-hidden />{i < STEPS.length - 1 && <ArrowRight className="hidden h-4 w-4 text-muted md:block" aria-hidden />}</div>
              <h3 className="title-4">{i + 1}. {title}</h3>
              <p className="caption-5 text-muted">{text}</p>
            </li>))}
        </ol>
        <p className="mt-4 caption-5 text-muted">It runs as the admin who switches it on, with that person's access, and stops if they are no longer an admin. A daily limit bounds the model use. Every start is in the audit log.</p>
      </Card>
      <AutomationPanel />
    </div>
  );
}
