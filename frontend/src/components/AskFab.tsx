import { MessageSquarePlus } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

/** A button that is always within reach, opening a new chat. It stays out of the way on the chat page itself. */
export default function AskFab() {
  const { pathname } = useLocation();
  if (pathname === "/ask" || pathname.startsWith("/ask/")) return null;
  return (
    <Link to="/ask" aria-label="Ask a question" title="Ask a question (new chat)"
      className="fixed bottom-5 right-5 z-30 inline-flex h-12 items-center gap-2 rounded-full bg-brand px-5 text-sm font-medium text-white shadow-lg transition hover:bg-brand-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
      <MessageSquarePlus className="h-5 w-5" /><span className="hidden sm:inline">Ask</span>
    </Link>
  );
}
