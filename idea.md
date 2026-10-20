# RepoLens AI
### AI-Powered Repository Intelligence, Root-Cause Analysis & Bug Prevention

<p align="center">
  <strong>Understand your codebase. Investigate bugs. Validate fixes. Learn from every incident.</strong>
</p>

RepoLens AI is an open-source repository intelligence platform that combines Retrieval-Augmented Generation (RAG), code-aware search, Git history analysis, dependency graphs, multi-agent debugging, local memory, and AI usage analytics.

It helps developers understand large codebases, identify risky changes, investigate bugs across multiple repositories, and generate evidence-backed Root Cause Analysis (RCA) reports.

RepoLens AI also integrates with Jira to retrieve bug tickets and publish investigation results, connecting AI-powered debugging with the existing engineering workflow.

> **Core idea:** Turn repository knowledge, historical bugs, code changes, and engineering feedback into a continuously improving debugging system.

**Project status:** MVP development / proposed architecture  
**License:** Apache-2.0 (proposed)  
**Primary language:** Python  
**Deployment:** Local-first, Docker, and self-hosted environments

---

## Table of Contents

- [1. Vision](#1-vision)
- [2. Problem Statement](#2-problem-statement)
- [3. Project Goals](#3-project-goals)
- [4. Features](#4-features)
- [5. Product Architecture](#5-product-architecture)
- [6. Technology Stack](#6-technology-stack)
- [7. Repository Intelligence Engine](#7-repository-intelligence-engine)
- [8. RAG and Knowledge Graph](#8-rag-and-knowledge-graph)
- [9. Local Memory and Token Optimization](#9-local-memory-and-token-optimization)
- [10. Multi-Agent Investigation](#10-multi-agent-investigation)
- [11. Jira Integration](#11-jira-integration)
- [12. AI Usage and Cost Dashboard](#12-ai-usage-and-cost-dashboard)
- [13. Change Risk Prediction](#13-change-risk-prediction)
- [14. Fix Generation and Validation](#14-fix-generation-and-validation)
- [15. Continuous Learning](#15-continuous-learning)
- [16. Data Architecture](#16-data-architecture)
- [17. Security and Governance](#17-security-and-governance)
- [18. Repository Structure](#18-repository-structure)
- [19. Development Roadmap](#19-development-roadmap)
- [20. Evaluation Strategy](#20-evaluation-strategy)
- [21. Local Development](#21-local-development)
- [22. Image and Documentation Assets](#22-image-and-documentation-assets)
- [23. Inspiration](#23-inspiration)
- [24. Contributing](#24-contributing)

---

## 1. Vision

Engineering organizations accumulate knowledge across source code, documentation, pull requests, commit history, issue trackers, and production incidents.

When a bug appears, developers must reconstruct that knowledge manually to understand what broke, which change introduced it, which components are affected, and how to fix it safely.

RepoLens AI aims to provide a unified intelligence layer across these sources.

The platform follows five principles:

1. **Understand:** Build a structured understanding of repositories, symbols, dependencies, APIs, and historical changes.
2. **Investigate:** Connect reported symptoms to relevant source code, commits, PRs, and historical incidents.
3. **Predict:** Identify potentially risky changes before they cause regressions.
4. **Validate:** Generate candidate fixes and challenge them using tests and independent review.
5. **Learn:** Preserve verified incident knowledge and improve future investigations using observed outcomes.

The long-term goal is to move beyond a codebase chatbot toward an evidence-driven bug prevention and debugging platform.

## 2. Problem Statement

Consider an engineering organization with more than 70 repositories.

A production issue appears:

> Device onboarding has started failing after yesterday's deployment. TLS handshakes are timing out intermittently.

An engineer must investigate several questions:

- Which repository implements the onboarding workflow?
- Which functions are responsible for TLS initialization?
- Which recent commits modified certificate handling?
- Which PR introduced a relevant behavioral change?
- Which other services depend on the modified component?
- Has a similar incident occurred previously?
- Which hypothesis has the strongest evidence?
- How can the proposed fix be validated without introducing another regression?

Traditional search, code review, and observability tools provide pieces of the answer, but the developer must connect them manually.

RepoLens AI combines repository intelligence, RAG, graph-based dependency analysis, Git history, bug memory, and multi-agent workflows to automate much of this investigation.

### Design principle

The system must distinguish between **observed facts, inferred relationships, and unverified hypotheses**. It should never present a plausible LLM-generated explanation as a confirmed root cause without supporting evidence.

## 3. Project Goals

### Goals

- Index multiple Git repositories and documentation sources.
- Provide semantic and exact-match code search.
- Understand Git commits, diffs, and pull requests.
- Build repository and symbol dependency graphs.
- Investigate issues using specialized AI agents.
- Persist local memory to reduce repeated inference.
- Measure token consumption by model, agent, and workflow.
- Integrate Jira tickets and RCA publishing.
- Predict change risk using explainable signals.
- Generate candidate fixes and validate them in isolated environments.
- Learn from confirmed outcomes and historical bug patterns.
- Support local-first and self-hosted deployments.

### Non-goals for the initial MVP

- Automatically merging generated code.
- Replacing existing CI, static analysis, or code review systems.
- Guaranteeing a correct diagnosis for every incident.
- Training a foundation model from scratch.
- Sending private repository content to an external provider without explicit configuration.
- Deploying a large collection of independent microservices before the core workflow is validated.

---

## 4. Features

| Feature | Description | Priority |
|---|---|---|
| Repository ingestion | Index local Git repositories and connected Git providers | P0 |
| Semantic code search | Search code and documentation using natural language | P0 |
| Git history intelligence | Index commits, PRs, and code diffs | P0 |
| RCA workflow | Investigate bugs and produce evidence-backed hypotheses | P0 |
| Local memory | Persist reusable summaries and historical incident knowledge | P0 |
| Token telemetry | Measure model usage, tokens, latency, and cost | P0 |
| Jira read integration | Retrieve issue descriptions, metadata, and comments | P0 |
| RCA publishing | Post approved investigation reports to Jira | P1 |
| Knowledge graph | Trace module, symbol, and repository dependencies | P1 |
| Change risk prediction | Rank risky changes before merge | P1 |
| AI usage dashboard | Explore cost and usage by model and workflow | P1 |
| Fix generation | Generate candidate patches and regression tests | P1 |
| Adversarial validation | Challenge candidate fixes using independent review | P1 |
| Outcome-based learning | Calibrate findings using verified results | P2 |
| Advanced CI integration | Trigger analysis on PRs and pipeline failures | P2 |

P0 represents the initial usable product, P1 the next development stage, and P2 later enhancements.

---

## 5. Product Architecture

### 5.1 High-level architecture

```mermaid
flowchart TB
    DEV["Developer / Engineering Team"]
    GIT["GitHub / GitLab / Local Git"]
    JIRA["Jira Issues and Comments"]
    CI["CI, Tests and Static Analysis"]

    subgraph APP["RepoLens AI Platform"]
        UI["Web Dashboard / CLI"]
        API["FastAPI Backend"]
        ING["Repository Ingestion Pipeline"]

        subgraph INTEL["Repository Intelligence"]
            PARSER["Code and Diff Parser"]
            RET["Hybrid Retrieval Engine"]
            GRAPH["Dependency Graph"]
            HIST["Git History Analyzer"]
        end

        subgraph AI["AI Intelligence Layer"]
            ORCH["LangGraph Orchestrator"]
            RCA["Investigation Agents"]
            FIX["Fix and Validation Agents"]
        end

        subgraph OPT["Cost and Memory Layer"]
            MEM["Local Memory"]
            CACHE["Semantic and Exact Cache"]
            TEL["Token Telemetry"]
        end

        GOV["Policy and Approval Gateway"]
    end

    subgraph STORE["Persistence"]
        PG[("PostgreSQL")]
        VEC[("ChromaDB")]
        GR[("NetworkX / Graph Store")]
    end

    DEV --> UI
    UI --> API
    GIT --> ING
    JIRA --> API
    ING --> PARSER
    PARSER --> RET
    PARSER --> GRAPH
    PARSER --> HIST

    API --> ORCH
    RET <--> VEC
    GRAPH <--> GR
    HIST --> PG

    ORCH --> RCA
    RCA --> FIX
    RCA <--> MEM
    MEM <--> CACHE
    CACHE --> PG

    ORCH --> TEL
    TEL --> PG
    ING --> PG
    API --> PG

    FIX --> GOV
    GOV --> CI
    GOV --> JIRA
    GOV --> PG
```

### 5.2 Architectural layers

**Integration layer**

Connects repositories, Git providers, Jira, and optional CI systems.

**Ingestion layer**

Parses source code, extracts symbols, processes diffs, generates embeddings, and updates indexes incrementally.

**Intelligence layer**

Combines semantic retrieval, lexical search, Git history, graph traversal, and historical incident retrieval.

**Agent layer**

Uses LangGraph to orchestrate investigation, hypothesis ranking, fix generation, adversarial review, and validation.

**Memory and optimization layer**

Stores reusable context, investigation summaries, cached results, and model usage records.

**Governance layer**

Controls access to repositories, code execution, Jira writes, PR creation, and other external actions.

### 5.3 Deployment strategy

Start with a modular monolith:

- One FastAPI application.
- One ingestion worker.
- One LangGraph orchestration runtime.
- PostgreSQL for persistent application data.
- ChromaDB for vector retrieval.
- A React dashboard when the API and workflows stabilize.

Use Docker Compose for local development. Introduce additional workers, persistent graph infrastructure, and Kubernetes deployment as actual workload requirements justify them.

---

## 6. Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Primary language | Python | Backend, indexing, and AI workflows |
| API framework | FastAPI | REST API and service integration |
| Agent orchestration | LangGraph | Stateful, conditional multi-agent workflows |
| LLM integration | Provider-neutral model adapter | Support hosted and local models |
| Embeddings | Configurable embedding model | Semantic representation of code and text |
| Vector database | ChromaDB | Vector search and semantic retrieval |
| Relational database | PostgreSQL | Metadata, jobs, investigations, and usage |
| Local cache | SQLite initially; Redis optionally | Persistent caching and coordination |
| Code parsing | Tree-sitter | Language-aware source parsing |
| Graph analysis | NetworkX initially | Dependency and relationship traversal |
| Graph database | Neo4j optionally | Larger persistent graph workloads |
| Git integration | Git CLI and provider APIs | Commits, branches, diffs, and repository state |
| Jira integration | Jira REST API | Issue retrieval and RCA publishing |
| Frontend | React + Next.js | Dashboard and investigation interface |
| Charts | Recharts or equivalent | Token and cost visualization |
| Testing | pytest | Unit, integration, and evaluation tests |
| Code quality | Ruff and mypy | Linting and static type checking |
| Deployment | Docker Compose | Reproducible development environment |
| Production deployment | Kubernetes optionally | Scalable self-hosted deployment |
| Observability | OpenTelemetry | Tracing, metrics, and operational visibility |

### Technology selection principles

- Keep the LLM provider interchangeable.
- Avoid unnecessary infrastructure in the MVP.
- Use structured parsing rather than treating all source code as plain text.
- Use persistent storage for durable memory.
- Keep cache invalidation explicit.
- Track actual token usage wherever provider APIs expose it.
- Add a dedicated graph database only when graph scale and query patterns justify it.

---

## 7. Repository Intelligence Engine

### 7.1 Ingestion pipeline

The ingestion pipeline transforms raw repository data into structured, searchable knowledge.

```mermaid
flowchart LR
    A["Repository Snapshot"] --> B["File Discovery"]
    B --> C["Language Detection"]
    C --> D["Code Parsing"]
    D --> E["Symbol Extraction"]
    E --> F["Semantic Chunking"]
    F --> G["Embedding Generation"]
    E --> H["Dependency Extraction"]
    G --> I["Vector Index"]
    H --> J["Knowledge Graph"]
    D --> K["Metadata and Source Maps"]
```

Responsibilities include:

1. Discover source files and relevant documentation.
2. Exclude binary files, generated artifacts, and irrelevant content.
3. Parse supported languages into functions, classes, modules, and symbols.
4. Create semantic chunks with source locations.
5. Index code, documentation, commits, and PR descriptions.
6. Extract imports, calls, and dependency relationships.
7. Record the exact repository revision associated with indexed content.
8. Update only changed artifacts when new commits arrive.

### 7.2 Code-aware chunking

Avoid arbitrary fixed-size chunks whenever semantic boundaries are available.

Prefer chunks representing:
- Functions and methods.
- Classes and interfaces.
- API handlers.
- Configuration blocks.
- Tests and fixtures.
- Documentation sections.

Store parent context, symbol identifiers, and relevant dependency metadata. A retrieved function should be traceable to its containing class, callers, tests, and associated Git changes.

### 7.3 Git history intelligence

For every indexed change, capture:

- Repository identifier.
- Commit SHA and parent SHA.
- Branch and timestamp.
- Changed files and symbols.
- Diff metadata.
- PR title, description, and review information.
- Related findings and investigations.
- Validation and fix outcomes.

Use immutable commit SHAs as source identifiers. Branch names alone are insufficient because branches change over time.

---

## 8. RAG and Knowledge Graph

### 8.1 Hybrid retrieval

RepoLens AI combines multiple retrieval strategies:

1. **Semantic retrieval:** Find code and documentation related to a natural-language question.
2. **Lexical retrieval:** Find exact symbols, identifiers, stack traces, and error messages.
3. **Metadata filtering:** Restrict results by repository, revision, language, path, or component.
4. **Graph traversal:** Retrieve related functions, dependent modules, and affected APIs.
5. **History retrieval:** Find relevant commits, PRs, and prior modifications.
6. **Incident memory:** Retrieve previously confirmed root causes and successful fixes.

A reranking stage can prioritize the most relevant results before they enter the LLM context.

### 8.2 Vector metadata

A code chunk may contain:

```json
{
  "organization_id": "org-001",
  "repository_id": "repo-001",
  "commit_sha": "abc123",
  "file_path": "src/tls/client.py",
  "symbol_name": "initialize_client",
  "language": "python",
  "start_line": 84,
  "end_line": 112,
  "content_hash": "example-hash",
  "chunk_type": "function"
}
```

These values are illustrative. Real metadata must come from the indexed repository.

### 8.3 Knowledge graph

Example node types:

- Organization
- Repository
- Commit
- Pull request
- File
- Module
- Function
- Class
- API endpoint
- Test
- Incident
- Bug pattern

Example relationships:

- `CONTAINS`
- `MODIFIES`
- `IMPORTS`
- `CALLS`
- `DEPENDS_ON`
- `IMPLEMENTS`
- `TESTS`
- `INTRODUCED`
- `REPORTED_IN`
- `FIXED_BY`
- `SIMILAR_TO`

Example investigation path:

```text
Incident: TLS handshake timeout
    |
    +-- affects --> Onboarding API
    |                  |
    |                  +-- calls --> Certificate Manager
    |                                    |
    |                                    +-- modified by --> Commit abc123
    |
    +-- resembles --> Historical Incident INC-042
                           |
                           +-- fixed by --> Commit def456
```

Graph relationships derived from static analysis or AI must retain provenance. They should not be treated as confirmed causal relationships without verification.

---

## 9. Local Memory and Token Optimization

Local memory is a core part of RepoLens AI. Its purpose is to reduce repeated model work, preserve engineering knowledge, and make investigations more consistent.

### 9.1 Memory types

**Repository memory**

Stores reusable repository summaries, architecture information, module descriptions, API contracts, and stable code facts.

**Investigation memory**

Stores previous RCA reports, hypotheses, relevant commits, confirmed fixes, failed approaches, and validation outcomes.

**Semantic memory**

Stores embeddings of reusable facts and incidents to support similarity-based retrieval.

**Prompt and response cache**

Stores reusable outputs for equivalent requests under compatible model, prompt, repository revision, and configuration versions.

**Working memory**

Stores the compact state of an active investigation, including relevant evidence references, decisions, and unresolved questions.

### 9.2 Memory architecture

```mermaid
flowchart TD
    Q["New Query or Jira Ticket"] --> C["Exact Cache Lookup"]
    C -->|Hit| R["Validate Cached Result"]
    C -->|Miss| M["Retrieve Local Memory"]
    M --> V["Semantic Retrieval"]
    V --> S["Select Relevant Evidence"]
    S --> P["Build Compact Prompt"]
    P --> L["LLM Inference"]
    L --> A["Validate and Structure Result"]
    A --> W["Persist Reusable Memory"]
    W --> T["Record Token Usage"]
    R --> T
```

### 9.3 Cache invalidation

Cached results must not silently survive meaningful changes.

Include relevant versions in cache keys:

- Repository commit SHA or content hash.
- Prompt template version.
- Model and provider configuration.
- Retrieval configuration.
- Task type.
- Relevant Jira issue version or content hash.
- Output schema version.

When code changes, invalidate affected repository facts and graph relationships. Historical incidents should remain available, but their relationship to the current code should be recalculated when necessary.

### 9.4 Reducing token consumption

- Retrieve only relevant symbols and documentation.
- Reuse stable repository summaries.
- Pass compact evidence references between agents.
- Avoid repeating the same tool outputs in every prompt.
- Cache deterministic parsing and analysis results.
- Reuse historical incident summaries.
- Route simple tasks to smaller or local models when quality is sufficient.
- Use stronger models only for tasks that benefit from deeper reasoning.
- Limit retries and redundant agent calls.
- Use provider-supported prompt caching where available.

ChromaDB provides semantic retrieval, not automatic prompt caching. Persistent memory, cache management, and invalidation should be separate application components.

### 9.5 Measuring memory effectiveness

Track:
- Cache hit rate.
- Number of avoided inference calls.
- Estimated tokens avoided.
- Actual input and output tokens.
- Cache invalidation rate.
- Cost per successful investigation.
- Investigation accuracy with and without memory.

A cache hit is not automatically a token saving. Estimate avoided tokens against a consistent baseline, and distinguish measured usage from estimates.

---

## 10. Multi-Agent Investigation

RepoLens AI uses LangGraph to coordinate specialized agents with bounded responsibilities.

### 10.1 Agent responsibilities

| Agent | Responsibility |
|---|---|
| Planner | Create the investigation plan |
| Scout | Discover relevant repositories and files |
| Cartographer | Trace dependencies and code relationships |
| Historian | Retrieve previous incidents and fixes |
| Triage | Normalize symptoms and prioritize evidence |
| Oracle | Estimate change risk |
| Investigator | Generate and rank root-cause hypotheses |
| Blue | Generate candidate patches and tests |
| Red | Challenge fixes and identify counterexamples |
| Judge | Evaluate validation results |
| Verifier | Check evidence and report consistency |
| Policy | Authorize external actions |
| Auditor | Record decisions and execution events |

Agents should not all receive the same context or call the LLM independently without a defined purpose. Each task should have a structured input, a structured output, and a clear termination condition.

### 10.2 Investigation workflow

```mermaid
flowchart TD
    A["Incident or Jira Ticket"] --> B["Planner"]
    B --> C["Retrieve Code, Git History and Memory"]
    C --> D["Triage and Rank Hypotheses"]
    D --> E{"Enough Evidence?"}

    E -->|No| F["Targeted Retrieval"]
    F --> D

    E -->|Yes| G["Generate RCA Report"]
    G --> H["Verifier"]
    H --> I{"Report Valid?"}

    I -->|No| F
    I -->|Yes| J["Human Review"]

    J --> K["Optional Fix Generation"]
    K --> L["Adversarial Review"]
    L --> M["Run Tests and Static Checks"]
    M --> N{"Validation Passed?"}

    N -->|No| O["Revise or Escalate"]
    N -->|Yes| P["Approval Gateway"]

    O --> J
    P --> Q["Publish to Jira"]
    Q --> R["Record Outcome in Memory"]
```

The initial MVP should implement repository retrieval, investigation, verification, and RCA reporting before enabling autonomous patch generation.

All loops require explicit retry limits, execution budgets, and escalation paths.

---

## 11. Jira Integration

Jira integration connects reported bugs to repository investigation and enables RepoLens AI to publish RCA findings directly into existing issue workflows.

### 11.1 Authentication

Support authentication appropriate to the Jira deployment:

- **Jira Data Center:** Personal access token (PAT), where supported by the deployment.
- **Jira Cloud:** API token with the appropriate account authentication method, or OAuth 2.0 where required.

A Data Center PAT must not be assumed to work with Jira Cloud.

Store tokens only in protected backend configuration or a secret manager. Never commit tokens, include them in source code, or expose them in frontend state or logs.

### 11.2 Read workflow

1. Configure the Jira base URL and authentication method.
2. Validate connectivity and permissions.
3. Fetch an issue by key or configured JQL.
4. Read permitted issue fields, descriptions, comments, and relevant metadata.
5. Extract symptoms, error messages, component names, and repository references.
6. Correlate the ticket with indexed repositories.
7. Start a RepoLens investigation.
8. Store the Jira issue key and investigation linkage.

### 11.3 RCA publishing workflow

1. Generate a structured RCA report.
2. Validate source citations and investigation status.
3. Display the report for review.
4. Obtain approval according to configured policy.
5. Publish an issue comment or approved field update.
6. Record the remote comment identifier and publishing result.
7. Save the final report and outcome in local memory.

### 11.4 Example RCA report

```markdown
## RepoLens AI — Root Cause Analysis

**Issue:** BUG-1234
**Status:** Investigation complete
**Root cause:** Not yet confirmed
**Confidence:** Medium

### Summary
Device onboarding requests are experiencing intermittent TLS timeouts.

### Leading hypothesis
A recent certificate-handling change may affect TLS initialization.

### Evidence
- Repository: device-onboarding
- Suspected commit: abc123
- Component: TLS client initialization
- Source: src/tls/client.py
- Evidence: [Source or diff link]

### Validation
- Existing tests: [Actual results]
- Regression test: [Actual results]
- Adversarial review: [Actual results]
- Production confirmation: Pending

### Recommended next steps
1. Reproduce the issue.
2. Validate the suspected change.
3. Add a regression test.
4. Review the proposed fix before merging.

### AI usage
- Models: [Actual model names]
- Input tokens: [Measured]
- Output tokens: [Measured]
- Estimated cost: [Calculated]

*AI-generated findings require engineering verification.*
```

The report must be generated from actual investigation data. Never fabricate commit IDs, test results, evidence links, or token usage.

### 11.5 Jira permissions

Start with read-only access. Add comment publishing as a separate permission. Restrict issue updates and transitions to explicit policies.

If a Jira request fails, record the error and retain the investigation report locally so that transient integration failures do not destroy investigation results.

---

## 12. AI Usage and Cost Dashboard

Every model invocation should emit a structured usage event. The dashboard aggregates these events into useful views for developers and administrators.

### 12.1 Dashboard views

**Overview**
- Total input and output tokens.
- Estimated and actual cost, where available.
- Request count and error rate.
- Cache hit rate and estimated tokens avoided.
- Cost per successful RCA.

**Model analytics**
- Provider and model.
- Input tokens.
- Output tokens.
- Cached input tokens, where reported.
- Request count.
- Latency and failures.
- Cost breakdown.

**Agent analytics**
- Token usage by Planner, Historian, Investigator, Blue, Red, Judge, and other agents.
- Most expensive tasks.
- Retry-related token consumption.
- Agent-level error rates.

**Workflow analytics**
- Repository indexing.
- Semantic search.
- PR analysis.
- Jira ticket investigation.
- RCA generation.
- Fix generation and validation.

**Budget controls**
- Daily and monthly budgets.
- Per-model limits.
- Per-workflow limits.
- Configurable alerts.
- Hard limits that stop optional inference work when exceeded.

### 12.2 Illustrative dashboard mockup

The following is a conceptual layout. Values and model names are placeholders, not actual usage measurements.

```text
+-----------------------------------------------------------+
| RepoLens AI                         AI Usage & Cost       |
+-----------------------------------------------------------+
| Total Tokens | Estimated Cost | Cache Hit Rate | Requests |
|    2.4M      |     $8.42      |      38%       |   1,240  |
+-----------------------------------------------------------+
| Token Usage by Model                                      |
|                                                           |
| Model A  ########################            52%           |
| Model B  ##############                      31%           |
| Model C  ########                            17%           |
+-----------------------------------------------------------+
| Usage by Workflow                                         |
|                                                           |
| RCA Investigation          $X.XX                          |
| Repository Indexing        $X.XX                          |
| PR Analysis                $X.XX                          |
| Jira Integration           $X.XX                          |
+-----------------------------------------------------------+
| Memory Efficiency                                         |
| Cache Hits | Tokens Avoided | Failed Requests | Latency    |
+-----------------------------------------------------------+
```

### 12.3 Token event schema

```json
{
  "event_id": "generated-uuid",
  "workflow_id": "investigation-id",
  "agent_name": "investigator",
  "provider": "configured-provider",
  "model_name": "configured-model",
  "input_tokens": 12000,
  "output_tokens": 1800,
  "cached_input_tokens": 0,
  "latency_ms": 4200,
  "cache_hit": false,
  "estimated_cost": 0.0,
  "usage_source": "provider_reported",
  "jira_issue_key": "BUG-1234"
}
```

The values are illustrative. The implementation must use actual provider usage data and configured pricing. The `estimated_cost` field should be calculated using a versioned pricing configuration rather than hardcoded.

Track embedding and reranking calls separately. Local inference should be distinguishable from hosted inference.

### 12.4 Primary efficiency metric

Use cost per successful investigation rather than token count alone:

\[
\text{Cost per successful RCA}
=
\frac{\text{Total inference cost}}{\text{Verified successful investigations}}
\]

This encourages optimization for reliable results, not simply fewer tokens.

---

## 13. Change Risk Prediction

The Oracle component analyzes proposed commits and PRs before merge.

### Signals

- Number of changed files and lines.
- Affected functions and public interfaces.
- Dependency graph centrality.
- Historical defects in related components.
- Relevant test coverage and failing tests.
- Similar past changes and their outcomes.
- Changes to configuration, API contracts, or security-sensitive paths.

### Initial scoring model

Start with an interpretable heuristic:

\[
R = 100 \times \sum_{i=1}^{n} w_i s_i
\]

Where:
- \(s_i\) is a normalized signal between 0 and 1.
- \(w_i\) is the configured weight.
- The weights sum to 1.

The resulting score is a prioritization heuristic, not automatically a probability of failure.

Every assessment should include its contributing signals, supporting evidence, model version, and known limitations.

Once sufficient labeled data exists, evaluate logistic regression or gradient-boosted models and calibrate predicted probabilities against observed outcomes.

---

## 14. Fix Generation and Validation

RepoLens AI should generate candidate fixes only after gathering sufficient evidence.

### Blue — Fix generation

- Receive a selected root-cause hypothesis.
- Retrieve relevant source and tests.
- Create an isolated Git worktree.
- Generate a minimal patch.
- Propose regression tests.
- Explain the expected behavior.

### Red — Adversarial review

- Challenge the diagnosis.
- Search for counterexamples.
- Identify missing edge cases.
- Review compatibility and error handling.
- Suggest tests that could disprove the proposed fix.

### Judge — Validation

- Apply the patch in an isolated environment.
- Run relevant tests and static analysis.
- Compare behavior against the original regression.
- Check the acceptance criteria.
- Escalate unresolved failures.

Possible validation states:

- `PROPOSED`
- `VALIDATING`
- `PASSED`
- `FAILED`
- `NEEDS_HUMAN_REVIEW`
- `CANCELLED`

A passing test suite is useful evidence, not proof of correctness. Automatically merging or deploying patches is outside the initial MVP.

---

## 15. Continuous Learning

RepoLens AI should improve its investigations using verified outcomes rather than simply accumulating LLM-generated summaries.

### Bug memory

Store:
- Bug class and normalized symptoms.
- Affected repositories and symbols.
- Relevant commits and PRs.
- Confirmed root cause, if known.
- Failed hypotheses.
- Accepted fixes and regression tests.
- Validation results.
- Follow-up regressions and rollbacks.

### Learning workflow

```mermaid
flowchart LR
    A["New Incident"] --> B["Investigate"]
    B --> C["Propose Fix"]
    C --> D["Validate"]
    D --> E["Human Review"]
    E --> F["Observe Outcome"]
    F --> G["Update Memory"]
    G --> H["Calibrate Retrieval and Risk"]
    H --> A
```

Distinguish a proposed fix, an accepted PR, a passing test suite, and a confirmed resolution. They represent different levels of evidence.

Use historical outcomes to improve retrieval and risk calibration. Do not treat an LLM's own confidence as a verified label.

---

## 16. Data Architecture

PostgreSQL stores structured application records. ChromaDB stores vector embeddings. NetworkX initially provides graph analysis in the application runtime.

### Proposed entities

| Entity | Purpose |
|---|---|
| `organizations` | Workspace and tenant metadata |
| `repositories` | Repository configuration and indexed revision |
| `index_runs` | Ingestion state and errors |
| `code_chunks` | Source references and vector identifiers |
| `changes` | Commits, PRs, and diff metadata |
| `findings` | Reported and detected issues |
| `investigations` | Workflow state and reports |
| `hypotheses` | Candidate root causes and evidence |
| `fix_attempts` | Proposed patches and validation outcomes |
| `memory_entries` | Reusable repository and incident memory |
| `cache_entries` | Cache keys, versions, expiry, and results |
| `llm_call_events` | Model usage and cost telemetry |
| `jira_connections` | Jira configuration and secret references |
| `jira_issue_links` | Jira issues linked to investigations |
| `jira_publish_events` | Comment publishing and approval results |
| `audit_events` | Decisions and external actions |

Use database migrations to evolve the schema. Store only references to protected credentials, not plaintext tokens in ordinary application tables.

---

## 17. Security and Governance

Repository contents, issue descriptions, and retrieved documentation must be treated as untrusted input.

### Requirements

- Read-only operation by default.
- Least-privilege Git and Jira credentials.
- Secure secret storage and token rotation.
- Organization-level authorization.
- No secrets in logs or LLM prompts unless explicitly necessary and permitted.
- Isolated execution of generated code.
- Network restrictions and resource limits for test sandboxes.
- Explicit approval before posting to Jira or creating PRs.
- Audit records for privileged operations.
- Configurable data retention and deletion.
- Clear separation between model output and verified facts.

### Policy gateway

```text
Agent proposes an action
          |
          v
Validate identity and permissions
          |
          v
Check action policy and approval
          |
          v
Enforce execution limits
          |
          v
Execute authorized operation
          |
          v
Record audit event
```

The gateway should deny unknown or unauthorized actions by default.

For local-first deployments, external inference and telemetry must be configurable. Do not claim that source code remains local unless the complete execution path has been verified.

---

## 18. Repository Structure

```text
repolens.ai/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
│
├── apps/
│   ├── api/
│   └── web/
│
├── src/
│   └── repolens/
│       ├── config/
│       ├── domain/
│       ├── integrations/
│       │   ├── git/
│       │   ├── github/
│       │   ├── gitlab/
│       │   └── jira/
│       ├── ingestion/
│       ├── parsing/
│       ├── retrieval/
│       ├── graph/
│       ├── agents/
│       ├── workflows/
│       ├── memory/
│       ├── cache/
│       ├── telemetry/
│       ├── governance/
│       ├── execution/
│       └── storage/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── evaluation/
│
├── evals/
│   ├── retrieval/
│   ├── root_cause/
│   ├── risk_scoring/
│   └── fix_validation/
│
├── docs/
│   ├── architecture.md
│   ├── threat-model.md
│   ├── development.md
│   ├── jira-integration.md
│   └── decisions/
│
└── scripts/
    ├── index_repository.py
    └── seed_demo.py
```

This is the proposed target layout. Start with a smaller set of modules and split them as their responsibilities stabilize.

---

## 19. Development Roadmap

### Phase 1 — Repository intelligence

- Local Git ingestion.
- Semantic chunking and ChromaDB indexing.
- Code and documentation search.
- Source citations.
- Basic API and CLI.

**Milestone:** Ask a question and retrieve relevant code with accurate source references.

### Phase 2 — Git history and RCA

- Commit and PR indexing.
- Diff analysis.
- LangGraph orchestration.
- Investigation and verification agents.
- Structured RCA reports.

**Milestone:** Investigate a seeded regression and retrieve the relevant code and commit.

### Phase 3 — Local memory and usage analytics

- Persistent repository summaries.
- Historical incident memory.
- Version-aware caching.
- Provider usage instrumentation.
- Model, agent, and workflow cost tracking.
- Initial usage dashboard.

**Milestone:** Demonstrate measurable reduction in repeated inference and report actual model usage.

### Phase 4 — Jira integration

- Jira connection settings.
- Data Center PAT support.
- Appropriate Jira Cloud authentication.
- Issue retrieval and JQL-based discovery.
- Ticket-to-repository correlation.
- RCA comment publishing with approval.

**Milestone:** Retrieve a Jira bug, investigate it, and publish a verified report to the ticket.

### Phase 5 — Graph intelligence and risk analysis

- Symbol and dependency extraction.
- Cross-repository graph traversal.
- Change risk scoring.
- Historical defect correlation.

**Milestone:** Explain which components may be affected by a proposed change and why.

### Phase 6 — Fix generation and adversarial validation

- Isolated worktree execution.
- Candidate patch generation.
- Adversarial review.
- Test execution and validation.
- Human approval workflow.

**Milestone:** Generate a candidate fix for a known regression and validate it without modifying protected branches.

### Phase 7 — Production readiness and learning

- Multi-organization isolation.
- CI and PR integrations.
- Outcome-based calibration.
- Operational observability.
- Deployment documentation.
- Kubernetes support where justified.

A focused solo-developer POC can target approximately six weeks for a narrow initial workflow, with the remaining phases delivered iteratively.

---

## 20. Evaluation Strategy

Evaluate the system using known regressions rather than subjective judgments about response quality.

### Retrieval metrics
- Recall@K.
- Mean reciprocal rank.
- Citation accuracy.
- Indexing latency.
- Incremental indexing cost.

### RCA metrics
- Top-1 and Top-5 root-cause accuracy.
- Evidence support rate.
- Unsupported hypothesis rate.
- Time to a useful report.
- Human verification rate.

### Cost metrics
- Input and output tokens per investigation.
- Cost per successful RCA.
- Cache hit rate.
- Estimated tokens avoided.
- Retry overhead.
- Cost by model and agent.

### Fix metrics
- Candidate patches that apply successfully.
- Regression tests that detect the original bug.
- Validation pass and failure rates.
- Newly introduced failures.
- Human acceptance rate.

### Benchmark design

Build a small dataset of repositories with deliberately introduced bugs. Record the introducing commit, affected symbols, failing tests, expected diagnosis, and known fix.

Compare:
1. Keyword search.
2. Semantic RAG.
3. RAG plus Git history.
4. RAG plus dependency graph.
5. Full investigation with local memory.

This establishes whether each architectural component improves accuracy or reduces cost.

---

## 21. Local Development

### Prerequisites

- Python 3.11 or a compatible supported version.
- Git.
- Docker and Docker Compose.
- A configured LLM provider or local inference service.
- A compatible embedding model.

### Clone the repository

```bash
git clone https://github.com/gnsaddy/repolens.ai.git
cd repolens.ai
```

### Create a virtual environment

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1
```

### Install development dependencies

Once the package configuration is implemented:

```bash
pip install -e ".[dev]"
```

### Configure environment variables

Create `.env` based on `.env.example`.

```dotenv
APP_ENV=development
LOG_LEVEL=INFO

DATABASE_URL=postgresql://repolens:repolens@localhost:5432/repolens
VECTOR_STORE=chroma
CHROMA_PERSIST_DIRECTORY=.data/chroma

LLM_PROVIDER=local
LLM_MODEL=
EMBEDDING_MODEL=

MEMORY_BACKEND=local
CACHE_ENABLED=true

JIRA_BASE_URL=
JIRA_AUTH_TYPE=
JIRA_PROJECT_KEYS=
JIRA_SECRET_REFERENCE=

TELEMETRY_ENABLED=true
```

These are example configuration names. A working implementation must define their validation rules and supported values. Do not commit real credentials.

### Run tests

```bash
pytest
```

### Planned CLI experience

```bash
repolens repo add /path/to/repository
repolens repo index
repolens search "Where is TLS certificate validation performed?"
repolens investigate "Why is device onboarding timing out?"
repolens jira fetch BUG-1234
repolens jira rca BUG-1234
repolens usage report
```

These commands describe the intended interface and require implementation before use.

---

## 22. Image and Documentation Assets

Architecture diagrams are embedded using Mermaid so GitHub can render them directly in the README. Dedicated product screenshots and visual assets can be added as the interface is implemented.

Recommended assets:

| Asset | Suggested path | Purpose |
|---|---|---|
| Product hero banner | `docs/assets/repolens-hero.png` | Repository overview and project branding |
| System architecture | `docs/assets/architecture.png` | Presentation-ready architecture image |
| RCA workflow | `docs/assets/rca-workflow.png` | Investigation lifecycle |
| Knowledge graph | `docs/assets/knowledge-graph.png` | Repository relationship visualization |
| Usage dashboard | `docs/assets/usage-dashboard.png` | Token and cost analytics |
| Jira integration | `docs/assets/jira-integration.png` | Ticket-to-RCA workflow |
| Investigation demo | `docs/assets/investigation-demo.gif` | Short product walkthrough |

Only add Markdown image links once the corresponding files exist. Screenshots should reflect actual application behavior; illustrative mockups should be labeled as such.

---

## 23. Inspiration

RepoLens AI takes conceptual inspiration from [Bug Immune System](https://bugimmunesystem.com/) and its closed-loop approach to software reliability.

The project adapts related ideas—change-risk prediction, bug memory, adversarial validation, outcome-driven learning, and controlled execution—to an open-source repository intelligence workflow.

RepoLens AI is an independent project and does not claim feature parity with the referenced product.

The intended differentiation is the combination of:

- Cross-repository RAG and code intelligence.
- Git history and dependency-aware investigation.
- Persistent local memory to reduce repeated inference.
- Per-model and per-agent token analytics.
- Jira ticket ingestion and RCA publishing.
- Evidence-backed multi-agent debugging.
- Controlled fix generation and validation.

---

## 24. Contributing

Contributions are welcome as the implementation becomes available.

Potential areas include:
- Language parsers.
- Git provider integrations.
- Retrieval and reranking.
- Graph extraction.
- Local memory and cache optimization.
- Jira integration.
- Dashboard and visualization.
- Investigation evaluation.
- Secure code execution.
- Documentation and reproducible bug benchmarks.

### Contribution principles

1. Keep modules small and testable.
2. Preserve source provenance for important claims.
3. Use structured agent outputs.
4. Avoid unapproved write operations.
5. Test cache invalidation and repository revision handling.
6. Measure new features against established baselines.
7. Document security and architectural decisions.

See `CONTRIBUTING.md` for contribution guidelines as they are implemented.

---

## Final Direction

RepoLens AI should begin with one measurable workflow:

**Jira bug report → repository and history retrieval → ranked RCA hypotheses → evidence-backed report → approved Jira comment.**

Local memory and token telemetry should be implemented alongside the core investigation workflow so that the platform can measure its efficiency from the beginning.

Once that workflow is reliable, expand into graph-aware risk prediction, adversarial fix validation, and outcome-based learning.

The long-term objective is to help engineering teams not only find bugs faster, but also reduce repeated investigations, control AI costs, and make safer code changes with stronger evidence.

