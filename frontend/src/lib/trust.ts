// What the Security and FAQ pages say about controls and trust. One place, so the pages cannot disagree, and a test checks it.
// Each statement must stay true: change it only with the code that backs it (SECURITY.md lists where), and never mark something
// "built" that is not, or name a certification we do not hold.

export type Status = "built" | "partly" | "not-yet";
export const STATUS_LABEL: Record<Status, string> = { built: "Built", partly: "Partly", "not-yet": "Not yet" };

export const CONTROLS: { name: string; status: Status; note: string }[] = [
  { name: "Roles and per-repository access", status: "built", note: "Admin, member and viewer, plus repositories restricted to chosen people. Every sensitive action is checked and recorded, and an automated test fails the build if an endpoint skips the check." },
  { name: "Authenticator-app sign-in", status: "built", note: "Optional for each person, with one-time recovery codes." },
  { name: "Single sign-on (OpenID Connect)", status: "partly", note: "Works and is tested, but is set up through the API; there is no settings page for it yet." },
  { name: "SAML single sign-on", status: "not-yet", note: "Only OpenID Connect is supported." },
  { name: "SCIM user provisioning", status: "built", note: "SCIM 2.0 users, with a token an admin creates." },
  { name: "Audit log", status: "built", note: "Admins can read and export who did what, what was refused and failed sign-ins. Passwords and keys are never written to it." },
  { name: "Secrets masked before indexing and before any model sees text", status: "built", note: "High-confidence keys and tokens are removed. This is pattern-based, so an unusual secret format can slip through." },
  { name: "Encryption of saved credentials", status: "built", note: "Git, Jira, Confluence and model credentials are encrypted at rest and never returned by the API." },
  { name: "Encryption in transit (HTTPS)", status: "partly", note: "RepoLens serves plain HTTP behind your proxy. The deployment guide covers certificates (your own, cert-manager or Caddy), but you set them up." },
  { name: "Encryption of the database and its disks", status: "not-yet", note: "Depends on your infrastructure, for example encrypted volumes. RepoLens does not add it." },
  { name: "Runs in your own infrastructure", status: "built", note: "Self-hosted with Docker Compose or Kubernetes, and it works without any AI service." },
  { name: "No telemetry", status: "built", note: "No analytics, usage pings or crash reports go to the authors. Tracing only goes where an administrator points it." },
  { name: "Deleting your data", status: "built", note: "One command deletes a whole workspace, and old records are cleaned up on a schedule you set." },
  { name: "Exporting your data", status: "partly", note: "The audit log exports as CSV. There is no one-click export of everything yet." },
  { name: "Private vulnerability reporting", status: "partly", note: "The route is described on this site. It works once the maintainer has switched on GitHub's private reporting for the repository." },
  { name: "Independent penetration test", status: "not-yet", note: "None yet. The automated security tests are in the repository for you to read and run." },
  { name: "SOC 2, ISO 27001 or similar certification", status: "not-yet", note: "No audit has started and there is no certification." },
];

export const FAQ: { id: string; q: string; a: string }[] = [
  { id: "safe", q: "Is RepoLens safe to use?", a: "You can check that instead of taking our word for it. It is open source under the Apache License 2.0, so the code is fully auditable, and it runs on infrastructure you control. It is also a beta, with no independent security audit yet. The Security page lists every control and says which are built, partly built or not built." },
  { id: "send", q: "Does RepoLens send my code or documents anywhere?", a: "Not by itself. Search, ranking and git-history analysis run on your own server without calling any AI service. Data leaves only through destinations you set up, such as a language model you connect, a tool like GitHub or Jira you connect, or a draft pull request or ticket a person approves. The Security page has the full table, and Settings shows the live list for your workspace." },
  { id: "model", q: "Do I need an AI model?", a: "No. Without one you still get search, cited passages, the graph, drift alerts and investigations; a model only improves the wording of answers. You bring your own, so there is no lock-in." },
  { id: "sees", q: "If I connect a model, what does it see?", a: "The passages retrieved for a question, with keys and tokens masked first. Nothing else from your workspace is sent. Choose a provider whose terms you are comfortable with, or run a model on your own machine." },
  { id: "train", q: "Does RepoLens train on my data?", a: "RepoLens does not train models, and sends no usage data to its authors. A model provider you connect has its own terms for the text you send it, so read them." },
  { id: "who", q: "Who builds RepoLens, and is it legitimate?", a: "It is built by Aditya Raj (gnsaddy) as an open-source project. The code, the tests and the history are public, so you can judge it by what it does. It is not a company product and has no paid tier." },
  { id: "official", q: "What are the official channels, and how do I be sure I install the real one?", a: "The only official source is github.com/gnsaddy/repolens, and container images come from that project's own release workflow. Copies on other sites or under other names are not affiliated with it, so install only from there and check the repository address." },
  { id: "ready", q: "Is it ready for production?", a: "It is a v0.1 beta: tested and usable, but expect rough edges and changes. It is a good fit for a trial, a demo or a small team. Judge it against the control status table before you rely on it for anything sensitive." },
  { id: "wrong", q: "What if the AI gets something wrong?", a: "Answers cite the file and line they came from, every graph link says whether it was found or only guessed, and the confidence score is checked against what people confirmed. Fixes, reports and wiki edits are drafts until a person approves them, and nothing is merged for you." },
  { id: "delete", q: "Can I delete my data?", a: "Yes. A single command removes a whole workspace, and old records (feedback, audit events, ended sessions) are cleaned up on a schedule you set. Everything lives in your own PostgreSQL database, so deleting it removes everything." },
  { id: "size", q: "How big a repository can it handle, and how long does indexing take?", a: "Indexing takes about two milliseconds per chunk of code on a typical server, so a project of a few thousand files takes about a minute. Very large projects take many minutes. For a trial, pick a repository of a few thousand files." },
  { id: "cost", q: "What does it cost?", a: "The software is free (Apache License 2.0). You pay for the server you run it on and for any hosted model you choose to connect." },
  { id: "report", q: "How do I report a security problem?", a: "Privately, not in a public issue. Use the private reporting route described on the Security page." },
];
