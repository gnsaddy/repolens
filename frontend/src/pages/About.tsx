import { Link } from "react-router-dom";
import PublicLayout, { Points, REPO_URL, Section } from "@/components/PublicLayout";
import { ISSUES_URL } from "@/components/ReportIssue";
import { APP_VERSION } from "@/lib/version";

const DO: [string, string][] = [
  ["DeepMind Wiki", "A wiki built from your repositories, READMEs, Confluence, Jira, Slack, Notion, Google Docs and SharePoint. Browse it, ask it questions with cited answers, and see which pages may be out of date because the code they describe changed."],
  ["Knowledge graph", "How files, functions, documents and history connect, with facts kept apart from guesses, areas that hang together, hubs to change with care, and a Start here page for each repository."],
  ["Root-cause investigations", "From a bug report, CI log or Jira ticket to ranked suspects with the code, commits and pull requests behind them, plus a review step before anything is published."],
  ["Change risk and pull request reviews", "How risky a commit or pull request is, who knows the code, which tests cover it, and a GitHub Action that comments it on every pull request."],
  ["Your editor", "A server for the Model Context Protocol, so editors such as Claude Code and Cursor can search, ask and walk the graph with your own access."],
  ["Suggested fixes", "A drafted change for a confirmed cause that a person approves before it becomes a draft pull request. It is never merged for you."],
];

export default function About() {
  return (
    <PublicLayout title="About RepoLens AI" intro="A living wiki for your code and docs, with evidence-backed investigations on top. Open source, and built to be checked rather than trusted blindly.">
      <Section id="what" title="What it is">
        <p>RepoLens AI turns the places your team keeps knowledge (code, documentation, pull requests and tickets) into one searchable, connected base. You ask a question and get an answer with the file and line it came from. You change code and it tells you which pages may now be wrong. You hit a bug and it points at likely causes and shows its evidence.</p>
        <p>You bring your own AI model, or none: search, ranking and git-history analysis run on your own machine without calling any AI service. A model only improves the wording of answers.</p>
      </Section>

      <Section id="does" title="What you can do with it">
        <ul className="grid gap-3 sm:grid-cols-2">
          {DO.map(([t, d]) => <li key={t} className="rounded-lg border border-line p-4"><div className="font-medium text-ink">{t}</div><p className="mt-1 text-sm text-slate-600">{d}</p></li>)}
        </ul>
      </Section>

      <Section id="principles" title="How it is built to behave">
        <Points items={[
          <><b>Evidence over guesses.</b> Answers cite their sources, and every connection says whether it was found directly, inferred, or is only a weak guess.</>,
          <><b>People decide.</b> Fixes, reports and wiki edits are drafts until someone approves them.</>,
          <><b>Bring your own model.</b> No lock-in, and nothing leaves your server unless you connect something. <Link className="text-brand hover:underline" to="/security">See exactly what can leave</Link>.</>,
          <><b>Measured, not claimed.</b> Retrieval, answer memory, model routing and real-bug ranking are scored against recorded baselines, and the results, including the weak ones, are published in the repository's evaluation notes.</>,
        ]} />
      </Section>

      <Section id="status" title="Where it stands">
        <p>This is <b>{APP_VERSION}</b>. It is tested and usable, but early: expect rough edges and changes between releases. It has not had an independent security audit. The <Link className="text-brand hover:underline" to="/security">Security page</Link> lists what is and is not covered.</p>
        <p>Ideas for later, not promises: running a drafted fix's tests in a sandbox before a person reviews it, exporting wiki pages in an open knowledge format for other agents, and a faster incremental graph for very large repositories.</p>
      </Section>

      <Section id="built" title="Who built it and how it is licensed">
        <p>Built by Aditya Raj (<a className="text-brand hover:underline" href="https://github.com/gnsaddy" target="_blank" rel="noopener noreferrer">gnsaddy</a>) as an open-source project under the Apache License 2.0. It uses Python and FastAPI, PostgreSQL with pgvector, tree-sitter, and React with TypeScript.</p>
      </Section>

      <Section id="involved" title="Get involved">
        <Points items={[
          <><a className="text-brand hover:underline" href={REPO_URL} target="_blank" rel="noopener noreferrer">Read the code and the contribution guide</a>.</>,
          <><a className="text-brand hover:underline" href={`${ISSUES_URL}/new/choose`} target="_blank" rel="noopener noreferrer">Report a bug or request a feature</a>. Issues are public, so leave out private code and secrets.</>,
          <>Security problems go through the private route on the <Link className="text-brand hover:underline" to="/security#report">Security page</Link>, not a public issue.</>,
        ]} />
      </Section>
    </PublicLayout>
  );
}
