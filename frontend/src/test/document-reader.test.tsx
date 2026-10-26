import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import DocumentReader, { splitSections } from "@/components/DocumentReader";
import { FeedbackProvider } from "@/components/feedback";
import { AuthProvider } from "@/lib/auth";
import Knowledge from "@/pages/Knowledge";

afterEach(() => vi.restoreAllMocks());
const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "Content-Type": "application/json" } }));

const pdfMarkdown = (pages: number) => `> Imported from Report.pdf\n\n# Report\n\n${Array.from({ length: pages }, (_, i) => `## Page ${i + 1}\n\n${i === 6 ? "The retry policy uses exponential backoff, and backoff is capped." : `Text of page ${i + 1}.`}`).join("\n\n")}\n`;
const wikiPage = (markdown: string) => ({ repo_id: "col1", repo: "Learning SD", path: "Report.md", title: "Report", markdown, unresolved_links: [], breadcrumbs: [], outline: [], source: { type: "Uploaded", url: "", version: null, synced_at: null, updated_at: null, repo_url: "" }, backlinks: [], links_out: [], related: [], concepts: [], mentions_code: [] });

function reader(markdown: string, onClose = () => {}) {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => (String(input).includes("/wiki/page") ? json(wikiPage(markdown)) : json({})));
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><DocumentReader repoId="col1" path="Report.md" onClose={onClose} /></MemoryRouter></QueryClientProvider>);
}

describe("splitSections", () => {
  it("cuts at page headings, keeps what comes before, and leaves fenced code whole", () => {
    const s = splitSections("Intro line\n\n## One\n\ntext\n\n```\n## not a heading\n```\n\n## Two\n\nmore");
    expect(s.map((x) => x.title)).toEqual(["Start", "One", "Two"]);
    expect(splitSections("# My Title\n\nintro\n\n## One\n\nx").map((x) => x.title)).toEqual(["My Title", "One"]); // the title block is named after the title
    expect(s[1].text).toContain("## not a heading");
  });
  it("falls back to level-1 headings, and to one section when there are no headings", () => {
    expect(splitSections("# A\n\nx\n\n# B\n\ny").map((x) => x.title)).toEqual(["A", "B"]);
    expect(splitSections("just text").map((x) => x.title)).toEqual(["Document"]);
  });
});

describe("document reader", () => {
  it("shows a short document whole, with its contents, size and source", async () => {
    reader(pdfMarkdown(3));
    expect(await screen.findByRole("heading", { level: 2, name: "Report" })).toBeInTheDocument();
    expect(screen.getByText(/Report\.md · 4 sections · 1 KB/)).toBeInTheDocument(); // the title block and three pages
    const contents = screen.getByRole("navigation", { name: "Sections" });
    expect(within(contents).getAllByRole("button").map((b) => b.textContent)).toEqual(["Report", "Page 1", "Page 2", "Page 3"]);
    const text = screen.getByLabelText("Document text");
    expect(text).toHaveTextContent("Text of page 1.");
    expect(text).toHaveTextContent("Text of page 3."); // all of it, together
    expect(screen.getByText("Uploaded")).toBeInTheDocument();
  });

  it("opens a long document one section at a time, with next, previous and a contents list", async () => {
    reader(pdfMarkdown(30));
    const text = await screen.findByLabelText("Document text");
    expect(text).toHaveTextContent("Imported from Report.pdf");
    expect(text).not.toHaveTextContent("Text of page 1.");
    expect(screen.getByLabelText("Position")).toHaveTextContent("1 of 31"); // the title block plus 30 pages
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(text).toHaveTextContent("Text of page 1.");
    expect(text).not.toHaveTextContent("Text of page 2.");
    await userEvent.click(within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", { name: "Page 20" }));
    expect(text).toHaveTextContent("Text of page 20.");
    expect(screen.getByRole("button", { name: "Page 20" })).toHaveAttribute("aria-current", "true");
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(text).toHaveTextContent("Text of page 19.");
    await userEvent.click(screen.getByLabelText("Show all sections together"));
    expect(text).toHaveTextContent("Text of page 30.");
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
  });

  it("finds words across the whole document, lists where, and jumps to the match", async () => {
    reader(pdfMarkdown(30));
    await userEvent.type(await screen.findByLabelText("Find in document"), "backoff");
    const results = screen.getByRole("navigation", { name: "Search results" });
    expect(results).toHaveTextContent("2 matches in 1 section");
    expect(results).toHaveTextContent("Page 7");
    await userEvent.click(within(results).getByRole("button", { name: /Page 7/ }));
    expect(screen.getByLabelText("Document text")).toHaveTextContent("exponential backoff");
    await userEvent.clear(screen.getByLabelText("Find in document"));
    await userEvent.type(screen.getByLabelText("Find in document"), "zzz-nothing");
    expect(screen.getByRole("navigation", { name: "Search results" })).toHaveTextContent("Nothing in this document matches");
  });

  it("changes the text size, downloads the document, goes full screen and back", async () => {
    const onClose = vi.fn();
    reader(pdfMarkdown(3), onClose);
    const text = await screen.findByLabelText("Document text");
    expect(text.style.getPropertyValue("--reader-size")).toBe("16px");
    await userEvent.click(screen.getByRole("button", { name: "Larger text" }));
    await userEvent.click(screen.getByRole("button", { name: "Larger text" }));
    expect(text.style.getPropertyValue("--reader-size")).toBe("20px");
    await userEvent.click(screen.getByRole("button", { name: "Smaller text" }));
    expect(screen.getByLabelText("Current text size")).toHaveTextContent("18px");

    const saved: Blob[] = [];
    (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = (b) => { saved.push(b); return "blob:x"; };
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};
    await userEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(await saved[0].text()).toContain("## Page 2");

    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByRole("dialog", { name: "Document, full screen" })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Document, full screen" })).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");

    await userEvent.click(screen.getByRole("button", { name: "All documents" }));
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /Open in wiki/ })).toHaveAttribute("href", expect.stringContaining("/wiki?tab=browse&repo=col1&path=Report.md"));
  });

  it("explains a failure instead of showing a blank page", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ detail: "Page not found" }, 404));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><DocumentReader repoId="col1" path="gone.md" onClose={() => {}} /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });
});

describe("opening a document from the Documents tab", () => {
  it("reads it in place and comes back to the list", async () => {
    localStorage.setItem("repolens.token", "tok");
    const repo = { id: "col1", name: "Learning SD", source: "docs", url: "", default_branch: "main", indexed_sha: null, status: "ready", file_count: 1, chunk_count: 3, updated_at: new Date().toISOString() };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const u = String(input);
      if (u.endsWith("/auth/me")) return json({ id: "1", email: "a@b.io", name: "A", role: "member", org_id: "o" });
      if (u.endsWith("/repositories")) return json([repo]);
      if (u.includes("/wiki/page")) return json(wikiPage(pdfMarkdown(3)));
      if (u.endsWith("/documents")) return json([{ id: "d1", path: "Report.md", size: 604000, updated_at: new Date().toISOString(), deletable: true }]);
      return json({});
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><FeedbackProvider><MemoryRouter initialEntries={["/knowledge?tab=docs"]}><Knowledge /></MemoryRouter></FeedbackProvider></AuthProvider></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Read Report.md" }));
    expect(await screen.findByLabelText("Document text")).toHaveTextContent("Text of page 2.");
    await userEvent.click(screen.getByRole("button", { name: "All documents" }));
    expect(await screen.findByRole("button", { name: "Read Report.md" })).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Read this document here")); // the file name is a link too
    expect(await screen.findByLabelText("Document text")).toBeInTheDocument();
  });
});
