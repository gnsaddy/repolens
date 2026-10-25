import { Bug, ExternalLink, Lightbulb, MessageSquarePlus } from "lucide-react";
import { useState } from "react";
import Modal from "@/components/Modal";

// The project's public issue tracker. A fork or company copy can point this at its own tracker when building (VITE_ISSUES_URL).
export const ISSUES_URL = (import.meta.env?.VITE_ISSUES_URL as string | undefined) || "https://github.com/gnsaddy/repolens/issues";

const choices = [
  { icon: Bug, title: "Report a bug", text: "Something does not work as described.", href: `${ISSUES_URL}/new?template=bug_report.yml&labels=bug` },
  { icon: Lightbulb, title: "Request a feature", text: "An idea that would make RepoLens more useful to you.", href: `${ISSUES_URL}/new?template=feature_request.yml&labels=enhancement` },
];

/** Sidebar entry that opens the public issue forms. Nothing from the workspace is sent; the user types what they choose to share. */
export default function ReportIssue() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} className="mt-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-slate-300 hover:bg-white/10"><MessageSquarePlus className="h-4 w-4" />Report a bug / request a feature</button>
      {open && (
        <Modal title="Help improve RepoLens" subtitle="Opens the project's public issue form on GitHub in a new tab." onClose={() => setOpen(false)}>
          <ul className="space-y-3">
            {choices.map(({ icon: Icon, title, text, href }) => (
              <li key={title}><a href={href} target="_blank" rel="noopener noreferrer" className="flex items-start gap-3 rounded-lg border border-line p-3 hover:bg-slate-50">
                <Icon className="mt-0.5 h-5 w-5 text-brand" /><span className="flex-1"><span className="block text-sm font-medium">{title}</span><span className="block text-xs text-muted">{text}</span></span><ExternalLink className="h-4 w-4 text-muted" /></a></li>))}
          </ul>
          <p className="mt-4 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">Issues on GitHub are public. Nothing from your workspace is sent for you: do not paste private code, tickets, tokens or customer data. Security problems: please follow <a className="underline" href={`${ISSUES_URL.replace(/\/issues$/, "")}/blob/main/SECURITY.md`} target="_blank" rel="noopener noreferrer">SECURITY.md</a> instead.</p>
        </Modal>)}
    </>
  );
}
