import { Link } from "react-router-dom";
import PublicLayout, { Points, REPO_URL, SECURITY_MD_URL, Section } from "@/components/PublicLayout";

// Every statement on this page must stay true, and each is backed by code or a test (the table at the end of SECURITY.md lists where).
// Change this page and that table together. Do not add a certification or audit name unless one exists.
const FLOWS: [string, string, string][] = [
  ["A language model you choose", "The passages retrieved for a question, with keys and tokens masked", "Only if you connect a model. With none, nothing is sent."],
  ["An embedding or reranking service", "Text to be turned into vectors or re-scored", "Only if you set one up. The built-in embedder runs on your server."],
  ["GitHub, Bitbucket, Jira, Confluence, Slack, Notion, Google Docs, SharePoint", "Requests, using the token you saved, to read your own content", "Only for sources you connect."],
  ["A draft pull request on GitHub", "The change a person approved", "Only after approval. A public repository gets an extra warning and a second confirmation."],
  ["A Jira ticket or Confluence page", "A report or page a person approved", "Only after approval, and only if publishing is switched on for that connection."],
  ["Your RepoLens server, from the GitHub Action", "File names and line counts of a pull request", "Only if you add the workflow. On a public repository the comment is cut to the minimum."],
  ["A tracing service", "Request traces", "Only if an administrator sets an endpoint."],
  ["RepoLens' authors", "Nothing", "There is no analytics, usage ping or crash reporting."],
];

export default function Security() {
  return (
    <PublicLayout title="Security" intro="Short statements you can check against the code, including what we do not do. RepoLens is a beta, so we say plainly where it falls short.">
      <Section id="data" title="Where your data goes">
        <p>Your code and documents are indexed and stored on the server you run RepoLens on. These are the only ways anything leaves it:</p>
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-left text-sm" aria-label="Where data can go">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-muted"><tr><th className="px-3 py-2 font-medium">Destination</th><th className="px-3 py-2 font-medium">What is sent</th><th className="px-3 py-2 font-medium">When</th></tr></thead>
            <tbody>{FLOWS.map(([a, b, c]) => <tr key={a} className="border-t border-line align-top"><td className="px-3 py-2 font-medium">{a}</td><td className="px-3 py-2">{b}</td><td className="px-3 py-2 text-muted">{c}</td></tr>)}</tbody>
          </table>
        </div>
        <p>Signed in, <b>Settings → AI &amp; search → What leaves this server</b> shows the live list for your workspace.</p>
      </Section>

      <Section id="access" title="Who can do what">
        <Points items={[
          "Three roles (admin, member, viewer), checked against the database on every request, with a policy check and an audit entry for every sensitive action, including refusals.",
          "Repositories can be restricted to chosen people. Search, answers, the graph, investigations and counts all respect it, and hidden items answer as if they do not exist.",
          "Sign-in: passwords checked against a strength policy, optional authenticator-app codes with one-time recovery codes, single sign-on (OpenID Connect) and SCIM provisioning, sessions you can see and end, an idle timeout, and rate limits on sign-in and on the API.",
          "Invitations and password resets are single-use links that expire and are stored only as hashes. Access tokens for scripts and editors are shown once, stored as hashes, expire, and can never change account security.",
        ]} />
      </Section>

      <Section id="secrets" title="Keeping secrets out">
        <Points items={[
          "Keys and tokens found in text are masked before it is indexed and again before anything is sent to a model. Real key, certificate and environment files are never indexed.",
          "Credentials you save (Git hosts, Jira, Confluence, model providers) are encrypted at rest and never returned by the API.",
          "Addresses you enter for outbound connections are checked so the server cannot be pointed at internal networks.",
        ]} />
      </Section>

      <Section id="ai" title="The AI is not in charge">
        <Points items={[
          "Documents, code, tickets and logs are treated as untrusted text, never as instructions. Hostile text is kept on the data side of every prompt, and a test suite tries to trick it.",
          "Answers cite their sources, and made-up citations are flagged. Model output is read strictly and capped.",
          "A suggested fix is a draft. It must change existing text exactly, may not touch build, deployment, dependency or secret files, is dropped if it adds command execution or network calls, and is never run or merged by RepoLens. A person approves it, and a public repository needs a second confirmation.",
          "Reports and wiki edits are proposals until a person approves them.",
        ]} />
      </Section>

      <Section id="record" title="A record of what happened">
        <Points items={[
          "Admins can read and export an audit log of who did what, what was refused and failed sign-ins. Passwords and keys are never written to it.",
          "Old records are cleaned up on a schedule you can set, and a whole workspace can be deleted with one command.",
        ]} />
      </Section>

      <Section id="verify" title="Check it yourself">
        <Points items={[
          <>The source is open under the Apache License 2.0: <a className="text-brand hover:underline" href={REPO_URL} target="_blank" rel="noopener noreferrer">github.com/gnsaddy/repolens</a>.</>,
          "An automated test fails the build if any endpoint works without signing in, if a state-changing endpoint skips the policy check, or if a viewer can change anything.",
          "Every change runs lint, type checks, the test suites on two databases, browser tests, and a vulnerability scan of the container images.",
          <>The controls, and where each is implemented, are listed in <a className="text-brand hover:underline" href={SECURITY_MD_URL} target="_blank" rel="noopener noreferrer">SECURITY.md</a>.</>,
        ]} />
      </Section>

      <Section id="limits" title="Honest limits">
        <Points items={[
          "This is a beta release. It has not had an independent security audit and holds no compliance certification (such as SOC 2 or ISO 27001). Judge it by the code and the tests.",
          "You run it, so TLS, backups, secrets, updates and network exposure are yours to manage. The operations guide in the repository covers them.",
          "A model you connect sees the passages sent to it. Choose one you are comfortable with, or run without one.",
          "Fixes are never executed by RepoLens, so a person reviews them rather than a test run confirming them.",
          "Single sign-on is configured through the API; there is no settings page for it yet.",
        ]} />
      </Section>

      <Section id="report" title="Reporting a problem">
        <p>Please report vulnerabilities privately and do not open a public issue. Use <b>Report a vulnerability</b> on the repository's Security tab (GitHub's private reporting), or contact the maintainer through their GitHub profile. We will confirm receipt, fix it, and credit you if you wish.</p>
        <p>
          <a className="text-brand hover:underline" href={`${REPO_URL}/security/advisories/new`} target="_blank" rel="noopener noreferrer">Report a vulnerability privately</a>
          <span className="text-muted"> · opens GitHub in a new tab</span>
        </p>
      </Section>

      <Section id="official" title="Official channels">
        <p>The official source is <code>github.com/gnsaddy/repolens</code>. Copies on other sites or under other names are not affiliated with it, so install only from there. <Link className="text-brand hover:underline" to="/about">About RepoLens AI</Link></p>
      </Section>
    </PublicLayout>
  );
}
