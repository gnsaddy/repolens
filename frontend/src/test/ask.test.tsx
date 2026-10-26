import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import AskFab from "@/components/AskFab";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import type { ChatMsg, ChatThread } from "@/lib/api";
import Ask from "@/pages/Ask";

const json = (b: unknown, status = 200) => Promise.resolve(new Response(b === null ? null : JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));
beforeEach(() => localStorage.setItem("repolens.token", "tok"));
afterEach(() => vi.restoreAllMocks());

const repos = [{ id: "r1", name: "user-service", source: "local" }, { id: "c1", name: "handbook", source: "docs" }];
const now = () => new Date().toISOString();
const msg = (role: "user" | "assistant", content: string, extra: Partial<ChatMsg> = {}): ChatMsg => ({ id: `${role}-${Math.random().toString(36).slice(2, 8)}`, role, content, sources: [], connections: [], mode: role === "assistant" ? "extractive" : "", attachment: null, created_at: now(), edited_at: null, ...extra });
const SOURCES = [
  { id: "S1", repo: "handbook", repo_id: "c1", file: "runbook/deleted-users.md", kind: "doc", title: "Deleted users", start_line: 1, end_line: 9, node_id: "n1", url: null, snippet: "A deleted user gets a default profile" },
  { id: "S2", repo: "user-service", repo_id: "r1", file: "src/user_profile.py", kind: "code", title: "get_profile", start_line: 9, end_line: 14, node_id: "n2", url: null, snippet: "def get_profile(self, user_id):" },
];

/** A small fake of the chat API, holding chats in memory. */
function server(opts: { model?: boolean; initial?: ChatThread[]; gaps?: unknown[] } = {}) {
  const chats = new Map<string, ChatThread>((opts.initial ?? []).map((t) => [t.id, t]));
  const calls: { method: string; url: string; body?: Record<string, unknown> }[] = [];
  const summary = (t: ChatThread) => ({ ...t, items: undefined, messages: t.items.length, preview: t.items.at(-1)?.content.slice(0, 80) ?? "" });
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input), method = init?.method ?? "GET", body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    if (url.endsWith("/auth/me")) return json({ id: "u", email: "a@b.io", name: "A", role: "member", org_id: "o" });
    if (url.endsWith("/repositories")) return json(repos);
    if (url.endsWith("/chats/gaps")) return json({ items: opts.gaps ?? [] });
    if (method === "POST" && url.endsWith("/knowledge/collections/c1/documents")) return json({ received: 1, changed: 1, secrets_redacted: 0 });
    let m: RegExpMatchArray | null;
    if (method === "GET" && url.includes("/chats?")) {
      const archived = url.includes("archived=true"), q = decodeURIComponent(url.split("q=")[1] ?? "").toLowerCase();
      return json({ items: [...chats.values()].filter((t) => t.archived === archived && (!q || t.title.toLowerCase().includes(q))).map(summary), model_available: opts.model ?? false });
    }
    if (method === "POST" && url.endsWith("/chats")) {
      const t: ChatThread = { id: `t${chats.size + 1}`, title: "New chat", repo_ids: body.repo_ids, fallback: body.fallback, archived: false, created_at: now(), updated_at: now(), messages: 0, preview: "", items: [] };
      chats.set(t.id, t);
      return json(t, 201);
    }
    if ((m = url.match(/\/chats\/([^/]+)\/messages\/([^/]+)$/)) && method === "PUT") {
      const t = chats.get(m[1])!, i = t.items.findIndex((x) => x.id === m![2]);
      t.items = [{ ...t.items[i], content: body.content, edited_at: now() }, msg("assistant", `Answer to: ${body.content} [S1]`, { sources: SOURCES })];
      return json(t);
    }
    if ((m = url.match(/\/chats\/([^/]+)\/messages$/)) && method === "POST") {
      const t = chats.get(m[1])!;
      const u = msg("user", body.content, body.attachment ? { attachment: { name: body.attachment.name, chars: body.attachment.text.length } } : {});
      const a = msg("assistant", `Deleted users get a default profile. [S1]`, { sources: SOURCES });
      t.items = [...t.items, u, a];
      t.title = t.title === "New chat" ? body.content.slice(0, 40) : t.title;
      return json({ ...t, items: [u, a] }, 201);
    }
    if ((m = url.match(/\/chats\/([^/]+)$/))) {
      const t = chats.get(m[1]);
      if (!t) return json({ detail: "Chat not found" }, 404);
      if (method === "GET") return json(t);
      if (method === "PATCH") { Object.assign(t, body); return json(t); }
      if (method === "DELETE") { chats.delete(t.id); return json(null, 204); }
    }
    return json({});
  });
  return { calls, chats };
}
const thread = (over: Partial<ChatThread> = {}): ChatThread => ({ id: "t1", title: "Deleted users question", repo_ids: [], fallback: false, archived: false, created_at: now(), updated_at: now(), messages: 2, preview: "", items: [msg("user", "How are deleted users handled?"), msg("assistant", "Deleted users get a default profile. [S1]", { sources: SOURCES })], ...over });

function Where() { return <div data-testid="where">{useLocation().pathname}</div>; }
function page(route = "/ask") {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={[route]}>
    <Routes><Route path="/ask" element={<Ask />} /><Route path="/ask/:id" element={<Ask />} /></Routes><Where /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
}
const sendBox = () => screen.findByLabelText("Message");

describe("Ask: a new chat", () => {
  it("opens with an introduction, a scope list and a fallback switch that needs a model", async () => {
    server({ model: false });
    page();
    expect(await screen.findByText(/Ask anything about your connected repositories and documents/)).toBeInTheDocument();
    expect(screen.getByText(/to debug an error/)).toHaveTextContent(".txt");
    const scope = screen.getByLabelText("Scope");
    await screen.findByRole("option", { name: "handbook" }); // the repositories load a moment after the page
    expect(within(scope).getAllByRole("option").map((o) => o.textContent)).toEqual(["All repositories & knowledge base", "user-service", "handbook"]);
    const fb = screen.getByLabelText("Allow fallback knowledge");
    expect(fb).toBeDisabled(); // no language model is connected
    expect(fb.closest("label")).toHaveAttribute("title", expect.stringContaining("Needs a connected language model"));
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(); // nothing to send yet
  });

  it("explains fallback knowledge in an (i) tooltip that opens on click and closes on Escape", async () => {
    server({ model: true });
    page();
    const tip = await screen.findByRole("button", { name: "About fallback knowledge" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    await userEvent.click(tip);
    const box = screen.getByRole("tooltip");
    expect(box).toHaveTextContent("Not from your documents:");
    expect(box).toHaveTextContent("sent to your model provider even when nothing matched");
    expect(tip).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("enables the fallback switch when a model is connected", async () => {
    server({ model: true });
    page();
    await vi.waitFor(() => expect(screen.getByLabelText("Allow fallback knowledge")).toBeEnabled());
  });

  it("creates the chat on the first question, shows the answer with its sources and moves to the chat's address", async () => {
    const s = server();
    page();
    await screen.findByRole("option", { name: "user-service" });
    await userEvent.selectOptions(await screen.findByLabelText("Scope"), "r1");
    await userEvent.type(await sendBox(), "How are deleted users handled?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/ask\/t1$/)); // the chat has its own address
    const log = await screen.findByRole("log", { name: "Conversation" });
    expect(await within(log).findByText("How are deleted users handled?")).toBeInTheDocument();
    expect(await within(log).findByText(/Deleted users get a default profile/)).toBeInTheDocument();
    expect(within(log).getByText("extracted passages")).toBeInTheDocument();
    expect(s.calls.find((c) => c.method === "POST" && c.url.endsWith("/chats"))?.body).toEqual({ repo_ids: ["r1"], fallback: false }); // the scope chosen before the first question
    const sources = within(log).getByText("Sources (2)").closest("details")!;
    expect(within(sources).getByRole("link", { name: /handbook:runbook\/deleted-users.md:1/ })).toHaveAttribute("href", "/wiki?tab=browse&repo=c1&path=runbook%2Fdeleted-users.md"); // a document opens in the wiki
    expect(within(sources).getByRole("link", { name: /user-service:src\/user_profile.py:9/ })).toHaveAttribute("href", "/knowledge?tab=graph&node=n2"); // code opens in the graph
    expect((await screen.findByLabelText("Message") as HTMLTextAreaElement).value).toBe(""); // ready for the next question
  });

  it("sends with Enter and adds a line with Shift+Enter", async () => {
    const s = server();
    page();
    const box = await sendBox();
    await userEvent.type(box, "first line{Shift>}{Enter}{/Shift}second line");
    expect((box as HTMLTextAreaElement).value).toBe("first line\nsecond line");
    expect(s.calls.some((c) => c.url.endsWith("/messages"))).toBe(false);
    await userEvent.type(box, "{Enter}");
    await waitFor(() => expect(s.calls.find((c) => c.url.endsWith("/messages"))?.body).toEqual({ content: "first line\nsecond line" }));
  });

  it("offers example questions that fill the box", async () => {
    server();
    page();
    await userEvent.click(await screen.findByRole("button", { name: "How does the sign-in flow work?" }));
    expect((await sendBox() as HTMLTextAreaElement).value).toBe("How does the sign-in flow work?");
  });
});

describe("Ask: attaching a file to debug", () => {
  it("attaches a .log file, shows it, sends its text with the question and lets you remove it", async () => {
    const s = server();
    page();
    const input = await screen.findByLabelText("Attach file");
    expect(input).toHaveAttribute("accept", expect.stringContaining(".log"));
    await userEvent.upload(input, new File(["Traceback (most recent call last)\nTypeError: x is None"], "app.log", { type: "text/plain" }));
    expect(await screen.findByText("app.log")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove attachment" }));
    expect(screen.queryByText("app.log")).toBeNull();
    await userEvent.upload(input, new File(["TypeError: x is None"], "crash.txt", { type: "text/plain" }));
    await userEvent.type(await sendBox(), "Why does this fail?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(s.calls.find((c) => c.url.endsWith("/messages"))?.body).toEqual({ content: "Why does this fail?", attachment: { name: "crash.txt", text: "TypeError: x is None" } }));
    expect(await screen.findByText("crash.txt", { selector: "p" })).toBeInTheDocument(); // the file shows on the question
  });

  it("refuses other file types before anything is read or sent", async () => {
    const s = server();
    page();
    await userEvent.upload(await screen.findByLabelText("Attach file"), new File(["print(1)"], "script.py"), { applyAccept: false });
    expect(await screen.findByRole("alert")).toHaveTextContent("Only .txt and .log files can be attached.");
    expect(screen.queryByRole("button", { name: "Remove attachment" })).toBeNull();
    expect(s.calls.some((c) => c.url.endsWith("/messages"))).toBe(false);
  });
});

describe("Ask: history", () => {
  it("lists chats by when they were last used, opens one, and searches", async () => {
    const old = thread({ id: "t2", title: "Retry policy", updated_at: new Date(Date.now() - 20 * 86_400_000).toISOString() });
    const s = server({ initial: [thread(), old] });
    page("/ask/t1");
    const history = await screen.findByLabelText("Chats");
    expect(await within(history).findByText("Today")).toBeInTheDocument();
    expect(within(history).getByText("Older")).toBeInTheDocument();
    expect(within(history).getByRole("link", { name: /Deleted users question/ })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByText("How are deleted users handled?")).toBeInTheDocument();
    await userEvent.click(within(history).getByRole("link", { name: /Retry policy/ }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/ask/t2"));
    await userEvent.type(screen.getByLabelText("Search chats"), "retry");
    await waitFor(() => expect(s.calls.some((c) => c.url.includes("q=retry"))).toBe(true));
    await waitFor(() => expect(within(screen.getByLabelText("Chats")).queryByText(/Deleted users question/)).toBeNull());
  });

  it("starts a new chat from the New chat button", async () => {
    server({ initial: [thread()] });
    page("/ask/t1");
    await screen.findByText("How are deleted users handled?");
    await userEvent.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/ask$/));
    expect(await screen.findByText(/Ask anything about your connected repositories/)).toBeInTheDocument();
  });

  it("renames, archives, restores and deletes a chat from its menu", async () => {
    const s = server({ initial: [thread()] });
    page("/ask/t1");
    const history = await screen.findByLabelText("Chats");
    const menu = async () => userEvent.click(await within(history).findByRole("button", { name: "Options for Deleted users question" }));

    await menu();
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const title = screen.getByLabelText("Chat title");
    await userEvent.clear(title);
    await userEvent.type(title, "Profile defaults{Enter}");
    await waitFor(() => expect(s.calls.find((c) => c.method === "PATCH")?.body).toEqual({ title: "Profile defaults" }));
    expect(await within(history).findByText("Profile defaults")).toBeInTheDocument();

    await userEvent.click(await within(history).findByRole("button", { name: "Options for Profile defaults" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    await waitFor(() => expect(s.calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ archived: true }));
    await waitFor(() => expect(within(screen.getByLabelText("Chats")).queryByText("Profile defaults")).toBeNull()); // gone from the main list
    await userEvent.click(screen.getByRole("tab", { name: "Archived" }));
    const archived = await screen.findByLabelText("Archived chats");
    expect(await within(archived).findByText("Profile defaults")).toBeInTheDocument();
    await userEvent.click(within(archived).getByRole("button", { name: "Options for Profile defaults" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Restore" }));
    await waitFor(() => expect(s.calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ archived: false }));

    await userEvent.click(screen.getByRole("tab", { name: "Chats" }));
    await userEvent.click(await within(await screen.findByLabelText("Chats")).findByRole("button", { name: "Options for Profile defaults" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dlg = await screen.findByRole("alertdialog");
    expect(dlg).toHaveTextContent('Delete "Profile defaults"?');
    expect(s.calls.some((c) => c.method === "DELETE")).toBe(false); // nothing until the person confirms
    await userEvent.click(within(dlg).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(s.calls.some((c) => c.method === "DELETE" && c.url.endsWith("/chats/t1"))).toBe(true));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/ask$/)); // the open chat was deleted: back to a new one
  });

  it("archives and deletes the open chat from its own toolbar", async () => {
    const s = server({ initial: [thread()] });
    page("/ask/t1");
    await screen.findByText("How are deleted users handled?");
    await userEvent.click(screen.getByRole("button", { name: "Archive chat" }));
    await waitFor(() => expect(s.calls.find((c) => c.method === "PATCH")?.body).toEqual({ archived: true }));
    expect(await screen.findByText(/This chat is archived/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete chat" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(s.calls.some((c) => c.method === "DELETE")).toBe(true));
  });
});

describe("Ask: an open chat", () => {
  it("changes its scope and fallback setting", async () => {
    const s = server({ model: true, initial: [thread()] });
    page("/ask/t1");
    await screen.findByRole("option", { name: "handbook" });
    await userEvent.selectOptions(await screen.findByLabelText("Scope"), "c1");
    await waitFor(() => expect(s.calls.find((c) => c.method === "PATCH")?.body).toEqual({ repo_ids: ["c1"] }));
    await vi.waitFor(() => expect(screen.getByLabelText("Allow fallback knowledge")).toBeEnabled());
    await userEvent.click(screen.getByLabelText("Allow fallback knowledge"));
    await waitFor(() => expect(s.calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ fallback: true }));
  });

  it("edits a question, answers it again and marks it edited", async () => {
    const s = server({ initial: [thread()] });
    page("/ask/t1");
    await screen.findByText("How are deleted users handled?");
    await userEvent.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByLabelText("Edit your message");
    expect((box as HTMLTextAreaElement).value).toBe("How are deleted users handled?");
    await userEvent.clear(box);
    await userEvent.type(box, "Where is the profile service?");
    await userEvent.click(screen.getByRole("button", { name: "Save and answer again" }));
    await waitFor(() => expect(s.calls.find((c) => c.method === "PUT")?.body).toEqual({ content: "Where is the profile service?" }));
    expect(await screen.findByText("Where is the profile service?")).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByRole("log")).getAllByText(/Answer to: Where is the profile service\?/).length).toBeGreaterThan(0));
    expect(screen.getByRole("log").textContent?.match(/Answer to:/g)).toHaveLength(1); // answered once, not twice
    expect(screen.getByText("edited")).toBeInTheDocument();
    expect(screen.queryByText("How are deleted users handled?")).toBeNull();
  });

  it("can cancel an edit, and shows what could not be found", async () => {
    server({ initial: [thread({ items: [msg("user", "weird question"), msg("assistant", "Nothing relevant was found in the sources you can see.", { mode: "none" })] })] });
    page("/ask/t1");
    expect(await screen.findByText("no results")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit message" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Edit your message")).toBeNull();
    expect(screen.getByText("weird question")).toBeInTheDocument();
  });

  it("says plainly when a chat cannot be found", async () => {
    server();
    page("/ask/missing");
    expect(await screen.findByText("Chat not found")).toBeInTheDocument();
  });

  it("copies an answer", async () => {
    server({ initial: [thread()] });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    page("/ask/t1");
    await userEvent.click(await screen.findByRole("button", { name: "Copy answer" }));
    expect(writeText).toHaveBeenCalledWith("Deleted users get a default profile. [S1]");
  });
});

describe("the floating Ask button and the sidebar", () => {
  const fab = (route: string) => render(<MemoryRouter initialEntries={[route]}><AskFab /></MemoryRouter>);
  it("is on every page except the chat itself", () => {
    const { unmount } = fab("/wiki");
    expect(screen.getByRole("link", { name: "Open Ask chat" })).toHaveAttribute("href", "/ask");
    unmount();
    fab("/ask");
    expect(screen.queryByRole("link", { name: "Open Ask chat" })).toBeNull();
  });
  it("is hidden inside a chat too", () => {
    fab("/ask/t1");
    expect(screen.queryByRole("link", { name: "Open Ask chat" })).toBeNull();
  });

  it("appear in the signed-in app: an Ask item in the sidebar and the button on other pages, but not on the chat page", async () => {
    server();
    const open = (route: string) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><MemoryRouter initialEntries={[route]}><App /></MemoryRouter></AuthProvider></QueryClientProvider>);
    const first = open("/repositories");
    const items = await screen.findAllByRole("link", { name: /^Ask$/ }); // the sidebar, and the same menu on small screens
    expect(items.length).toBeGreaterThan(0);
    items.forEach((a) => expect(a).toHaveAttribute("href", "/ask"));
    expect(screen.getByRole("link", { name: "Open Ask chat" })).toHaveAttribute("href", "/ask"); // the floating button
    first.unmount();
    open("/ask");
    expect(await screen.findByRole("button", { name: "New chat" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Ask chat" })).toBeNull(); // not on top of the chat itself
    expect(screen.getAllByRole("link", { name: /^Ask$/ })[0]).toHaveAttribute("aria-current", "page"); // the sidebar item is highlighted
  });
});


describe("Ask: gaps", () => {
  const gap = { question: "How do I rotate the zebra gateway credentials?", count: 3, last_asked: now(), topic: "gateway, rotate, zebra", chat_id: "t9" };
  it("lists unanswered questions and drafts a page into a documentation collection", async () => {
    const s = server({ gaps: [gap] });
    page();
    await userEvent.click(await screen.findByRole("tab", { name: "Gaps" }));
    const list = await screen.findByLabelText("Unanswered questions");
    expect(list).toHaveTextContent("asked 3 times");
    expect(list).toHaveTextContent("Nobody else sees this list");
    expect(within(list).getByRole("link", { name: gap.question })).toHaveAttribute("href", "/ask/t9");
    await screen.findByRole("option", { name: "handbook" }).catch(() => undefined);
    await userEvent.click(within(list).getByRole("button", { name: /Draft a page/ }));
    await waitFor(() => expect(s.calls.find((c) => c.url.endsWith("/knowledge/collections/c1/documents"))).toBeTruthy());
    const doc = (s.calls.find((c) => c.url.endsWith("/documents"))!.body as { documents: { path: string; content: string }[] }).documents[0];
    expect(doc.path).toBe("drafts/how-do-i-rotate-the-zebra-gateway-credentials.md");
    expect(doc.content).toContain("asked 3 times");
  });
  it("says so when there is nothing to write", async () => {
    server();
    page();
    await userEvent.click(await screen.findByRole("tab", { name: "Gaps" }));
    expect(await screen.findByText(/No gaps found/)).toBeInTheDocument();
  });
});

describe("Ask: general knowledge label", () => {
  it("shows a 'Not from your documents' badge beside the answer label when the model used general knowledge", async () => {
    server({ initial: [thread({ items: [msg("user", "which model?"), msg("assistant", "Your documents do not say.\n\nNot from your documents: I am a language model.", { mode: "llm" })] })] });
    page("/ask/t1");
    const log = await screen.findByRole("log", { name: "Conversation" });
    expect(await within(log).findByTitle(/not backed by your sources/)).toHaveTextContent("Not from your documents");
  });
});

describe("Ask: names that were not found", () => {
  it("shows a 'Check these names' badge when the answer names things that are not in the repositories", async () => {
    server({ initial: [thread({ items: [msg("user", "how?"), msg("assistant", "It calls `ghost()`.\n\n_(Check these names, not found in your indexed repositories: `ghost()`)_", { mode: "llm" })] })] });
    page("/ask/t1");
    const log = await screen.findByRole("log", { name: "Conversation" });
    expect(await within(log).findByTitle(/not found in your indexed repositories/)).toHaveTextContent("Check these names");
  });

  it("shows no such badge for an ordinary answer", async () => {
    server({ initial: [thread({ items: [msg("user", "how?"), msg("assistant", "It calls create_user. [S1]", { mode: "llm" })] })] });
    page("/ask/t1");
    const log = await screen.findByRole("log", { name: "Conversation" });
    await within(log).findByText(/It calls create_user/);
    expect(within(log).queryByTitle(/not found in your indexed repositories/)).not.toBeInTheDocument();
  });
});

describe("Ask: the message box", () => {
  it("clears as soon as a question is sent and gives it back if sending fails", async () => {
    server();
    page();
    const box = await sendBox();
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return json({ id: "u", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (url.endsWith("/chats") && init?.method === "POST") return Promise.resolve(new Response(JSON.stringify({ detail: "boom" }), { status: 500, headers: { "Content-Type": "application/json" } }));
      return json({ items: [], model_available: false });
    });
    await userEvent.type(box, "Will this fail?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByLabelText("Message")).toHaveValue("Will this fail?"));
  });
  it("is empty while the answer is being prepared", async () => {
    let release: (r: Response) => void = () => undefined;
    const s = server();
    const base = globalThis.fetch;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => String(input).endsWith("/messages") ? new Promise<Response>((res) => { release = res; }) : base(input, init));
    void s;
    page();
    await userEvent.type(await sendBox(), "Slow question");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByLabelText("Message")).toHaveValue(""));
    expect(screen.getByText("Slow question")).toBeInTheDocument();
    release(new Response("{}", { status: 500 }));
  });
});

describe("Ask: a failed model call", () => {
  it("shows why the AI could not answer and offers to try again", async () => {
    const s = server({ initial: [thread({ items: [msg("user", "How are deleted users handled?", { id: "u1" }), msg("assistant", "**The AI model could not answer.** The provider rejected the API key. (model: gemini / x)\n\nWhat your sources contain on this (matching passages, not a written answer):\n\n- Deleted users get a default profile. [S1]", { mode: "failed", sources: SOURCES })] })] });
    page("/ask/t1");
    const log = await screen.findByRole("log", { name: "Conversation" });
    expect(await within(log).findByText("AI unavailable")).toBeInTheDocument();
    expect(log).toHaveTextContent("The provider rejected the API key");
    await userEvent.click(within(log).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(s.calls.find((c) => c.method === "PUT")?.body).toEqual({ content: "How are deleted users handled?" }));
  });
});

describe("Ask: save as a test", () => {
  it("saves the question with the files the answer cited", async () => {
    const s = server({ initial: [thread()] });
    page("/ask/t1");
    await userEvent.click(await screen.findByRole("button", { name: "Save as a test" }));
    await waitFor(() => {
      const c = s.calls.find((x) => x.url.endsWith("/golden/questions"));
      expect(c?.body).toEqual({ question: "How are deleted users handled?", expect_files: ["runbook/deleted-users.md"] });
    });
  });
});
