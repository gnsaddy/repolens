"""A small, fixed documentation set with questions in three styles, used to measure retrieval and memory matching.

Each topic is one page. `exact` reuses the page's words, `paraphrase` says the same thing in different words (the
hard case for keyword and hash embeddings), `confusable` asks something close but different that must NOT match the
same remembered answer. `unanswerable` questions have no page at all.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    path: str
    title: str
    body: str
    exact: str
    paraphrase: str
    confusable: str


TOPICS: list[Topic] = [
    Topic(
        "runbooks/restart-payments.md",
        "Restart the payments API",
        "Use this runbook when the payments API stops answering health checks. Run `kubectl rollout restart deploy/payments` and wait for the new pods to become ready. Never restart during the nightly settlement window.",
        "How do I restart the payments API?",
        "What is the procedure to bounce the payment service when it hangs?",
        "When is the nightly settlement window?",
    ),
    Topic(
        "policies/retry.md",
        "Retry policy",
        "The payment service retries failed requests three times with exponential backoff. The first wait is one second, then two, then four. After the third failure the request is sent to the dead letter queue.",
        "How many times does the payment service retry failed requests?",
        "How often are unsuccessful payment calls attempted again before giving up?",
        "What happens after the third failure?",
    ),
    Topic(
        "security/rotate-keys.md",
        "Rotate signing keys",
        "Signing keys are rotated every ninety days. Generate the new key with the key tool, publish it next to the old one for one day, then retire the old key. Rotation is owned by the security team.",
        "How often are signing keys rotated?",
        "What is the cadence for renewing our cryptographic keys?",
        "Who owns key rotation?",
    ),
    Topic(
        "onboarding/dev-setup.md",
        "Developer setup",
        "Install Python 3.12 and Node 20, clone the repository, run `make dev-api` for the backend and `make dev-web` for the frontend. The API listens on port 8000 and the web app on port 5173.",
        "How do I set up the development environment?",
        "What steps get a new engineer's laptop ready to run the project locally?",
        "Which port does the web app use?",
    ),
    Topic(
        "db/backups.md",
        "Database backups",
        "PostgreSQL is backed up nightly at two in the morning and kept for thirty days. Restore with `pg_restore` into a scratch database first and verify row counts before switching traffic.",
        "How long are database backups kept?",
        "For how many days do we retain the nightly dumps of our data store?",
        "How do I restore a database backup?",
    ),
    Topic(
        "oncall/escalation.md",
        "On-call escalation",
        "Page the primary on-call first. If there is no acknowledgement within ten minutes, page the secondary, then the engineering manager. Severity one incidents also notify the incident commander.",
        "Who do I page first during an incident?",
        "Whom should I alert initially when something breaks at night?",
        "How long before I escalate to the secondary?",
    ),
    Topic(
        "api/rate-limits.md",
        "API rate limits",
        "Clients may make six hundred requests per minute per address. Sign-in is limited to twenty attempts per minute. Responses over the limit return status 429 with a Retry-After header.",
        "What is the API rate limit per minute?",
        "How many calls can one client make each minute before being throttled?",
        "What status code is returned when the limit is exceeded?",
    ),
    Topic(
        "billing/invoices.md",
        "Invoice generation",
        "Invoices are generated on the first day of each month by the billing job. Each invoice lists usage by plan, applies credits, and is emailed to the account owner as a PDF.",
        "When are invoices generated?",
        "On what day of the month do customers get their bills produced?",
        "How is an invoice delivered to the customer?",
    ),
    Topic(
        "deploy/rollback.md",
        "Rolling back a release",
        "To roll back, run `kubectl rollout undo deploy/api` and confirm the previous image is running. Then open an incident note describing the bad release. Database migrations are not reversed automatically.",
        "How do I roll back a release?",
        "What is the way to revert a bad deployment to the previous version?",
        "Are database migrations reversed on rollback?",
    ),
    Topic(
        "search/indexing.md",
        "Search indexing",
        "Repositories are indexed incrementally by content hash, so unchanged files are skipped. A full reindex is needed only after changing the embedding model. Indexing runs in a background worker pool of four threads.",
        "When is a full reindex needed?",
        "Under what circumstances must everything be indexed again from scratch?",
        "How many threads does the indexing worker pool use?",
    ),
    Topic(
        "auth/sessions.md",
        "Sessions and sign-out",
        "Every sign-in creates a session that can be ended from the profile page. Sessions unused for eight hours expire. Changing the password signs out every other device.",
        "How long until an idle session expires?",
        "After how much inactivity is a user automatically logged out?",
        "What happens to other devices when the password changes?",
    ),
    Topic(
        "jira/publishing.md",
        "Publishing reports to Jira",
        "A root-cause report can be posted as a comment on a Jira issue after a person approves it. Publishing must be enabled on the connection and the report must pass citation verification.",
        "How do I publish a report to Jira?",
        "What is needed before an analysis can be written back onto a ticket?",
        "Does publishing need approval?",
    ),
    Topic(
        "wiki/confluence.md",
        "Importing from Confluence",
        "Connect Confluence with an API token, then import a page, a page tree or a whole space. Imported pages are restricted by default and re-synced on the schedule you choose.",
        "How do I import pages from Confluence?",
        "What is the way to bring a wiki space across from Atlassian?",
        "Are imported Confluence pages visible to everyone?",
    ),
    Topic(
        "llm/models.md",
        "Choosing models",
        "Connect several models and give each a tier. Light models answer simple questions, strong models handle hard investigations. Savings mode sends plain tasks one tier down.",
        "How do I choose which models are used?",
        "How can cheap and expensive language models be mixed for different jobs?",
        "What does savings mode do?",
    ),
    Topic(
        "llm/memory.md",
        "Answer memory",
        "Answers are remembered so a repeated question costs no tokens. A remembered answer is reused only while the indexed sources are unchanged and it is younger than seventy-two hours.",
        "How long are remembered answers kept?",
        "For what period is a stored answer valid before it is discarded?",
        "When is a remembered answer not reused?",
    ),
    Topic(
        "infra/cdn.md",
        "Content delivery",
        "Static assets are served from the edge cache with a one year lifetime and immutable file names. The HTML shell is never cached so releases take effect immediately.",
        "How long are static assets cached?",
        "What is the lifetime of cached front-end files at the edge?",
        "Is the HTML page cached?",
    ),
    Topic(
        "infra/logging.md",
        "Logging conventions",
        "Logs are written as JSON lines with a request id. Secrets are redacted before logging. Error logs are kept for ninety days and info logs for fourteen days.",
        "What format are logs written in?",
        "In what structure do services emit their log records?",
        "How long are error logs retained?",
    ),
    Topic(
        "team/code-review.md",
        "Code review rules",
        "Every change needs one approving review from a code owner. Reviews should be completed within one working day. Changes that touch authentication need a second reviewer from the security team.",
        "How many approvals does a change need?",
        "How many people must sign off on a pull request before it merges?",
        "Which changes need a second reviewer?",
    ),
    Topic(
        "data/retention.md",
        "Data retention",
        "Investigation reports are kept for one year. Remembered answers are kept for three days. Audit events are kept for two years and cannot be deleted by users.",
        "How long are investigation reports kept?",
        "How many months are the root-cause write-ups stored?",
        "Can users delete audit events?",
    ),
    Topic(
        "perf/cache-warm.md",
        "Warming the cache",
        "After a deploy, warm the cache by calling the health endpoint and the top ten search queries. A cold cache can double response time for the first five minutes.",
        "How do I warm the cache after a deploy?",
        "What should be done to prepare the cache once a new version is released?",
        "How long can a cold cache slow things down?",
    ),
    Topic(
        "sso/setup.md",
        "Single sign-on setup",
        "Configure an OpenID Connect provider with an issuer, client id and secret. Restrict sign-in to your e-mail domains. Users created through single sign-on are members, never admins.",
        "How do I set up single sign-on?",
        "What is needed to let staff log in through our identity provider?",
        "Can single sign-on users become admins?",
    ),
    Topic(
        "tests/running.md",
        "Running the tests",
        "Run `pytest` for the backend and `npm test` for the frontend. Use `make check` to run linting, types and all tests together. The suite needs no network access.",
        "How do I run the tests?",
        "Which commands execute the automated checks for the project?",
        "Does the test suite need network access?",
    ),
    Topic(
        "alerts/slo.md",
        "Service level objectives",
        "The API target is ninety-nine point nine percent availability per month and a ninety-fifth percentile latency under five hundred milliseconds. Burning the error budget pauses feature releases.",
        "What is the API availability target?",
        "What uptime percentage do we promise for the service each month?",
        "What happens when the error budget is used up?",
    ),
    Topic(
        "ownership/codeowners.md",
        "Code ownership",
        "Ownership is derived from commit history and a CODEOWNERS file. The person who changed a file most recently and most often is listed first when asking who to contact.",
        "How is code ownership determined?",
        "In what way does the system decide who looks after a file?",
        "Who is listed first as the contact?",
    ),
]

UNANSWERABLE: list[str] = [
    "What is the office wifi password?",
    "Which vendor do we use for employee payroll?",
    "How do I submit a travel expense report?",
    "What is the company holiday calendar for next year?",
    "Who won the football match last night?",
    "How do I configure the printer on the third floor?",
    "What is the parental leave policy?",
    "Which cafeteria serves lunch on Fridays?",
]
