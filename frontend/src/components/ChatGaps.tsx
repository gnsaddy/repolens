import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useConfirm, useToast } from "@/components/feedback";
import { Button, Spinner } from "@/components/ui";
import { api, type ChatGap, type Repo } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

const slug = (q: string) => q.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "question";

/** Questions your sources could not answer, most asked first: each one is a page worth writing. Built from your own chats only. */
export default function ChatGaps() {
  const toast = useToast();
  const confirm = useConfirm();
  const qc = useQueryClient();
  const gaps = useQuery({ queryKey: ["chat-gaps"], queryFn: () => api<{ items: ChatGap[] }>("/chats/gaps") });
  const repos = useQuery({ queryKey: ["repos"], queryFn: () => api<Repo[]>("/repositories") });
  const draft = useMutation({
    mutationFn: async (g: ChatGap) => {
      let target = (repos.data ?? []).find((r) => r.source === "docs");
      if (!target) {
        if (!(await confirm({ title: "Create a documentation collection?", message: "Drafts are saved as pages in a collection called “Handbook”. You can rename or delete it later.", confirmLabel: "Create Handbook" }))) return null;
        target = await api<Repo>("/knowledge/collections", { method: "POST", json: { name: "Handbook" } });
      }
      const path = `drafts/${slug(g.question)}.md`;
      const body = `# ${g.question.replace(/\s+/g, " ").trim()}\n\n> Draft started from a question that was asked ${g.count} time${g.count === 1 ? "" : "s"} and not answered by the existing documents. Replace this note with the answer.\n\n## Answer\n\n_Write the answer here: what to do, who owns it, and where it lives in the code._\n`;
      await api(`/knowledge/collections/${target.id}/documents`, { method: "POST", json: { documents: [{ path, content: body }] } });
      return `${target.name}/${path}`;
    },
    onSuccess: (where) => { if (where) { toast(`Draft saved as ${where}. Open Knowledge to write it.`); qc.invalidateQueries({ queryKey: ["repos"] }); } },
    onError: (e: Error) => toast(e.message),
  });
  if (gaps.isLoading) return <Spinner />;
  const items = gaps.data?.items ?? [];
  if (!items.length) return <p className="p-2 text-sm text-muted">No gaps found. Questions your sources cannot answer will be listed here so you know which pages to write.</p>;
  return (
    <div aria-label="Unanswered questions">
      <p className="mb-2 px-2 text-xs text-muted">From your own chats only. Nobody else sees this list.</p>
      <ul className="space-y-2">{items.map((g) => (
        <li key={g.chat_id + g.question} className="rounded-lg border border-line p-2.5 text-sm">
          <Link to={`/ask/${g.chat_id}`} className="block font-medium hover:text-brand">{g.question}</Link>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
            <span>{g.count === 1 ? "asked once" : `asked ${g.count} times`}</span><span>{timeAgo(g.last_asked)}</span><span className="truncate">topic: {g.topic}</span></div>
          <Button size="sm" variant="outline" className="mt-2" disabled={draft.isPending} onClick={() => draft.mutate(g)}><FilePlus2 className="h-3.5 w-3.5" />Draft a page</Button>
        </li>))}</ul>
    </div>
  );
}
