export interface User { id: string; email: string; name: string; role: "admin" | "member" | "viewer"; org_id: string; auth_source?: string; totp_enabled?: boolean }
export interface Repo {
  id: string; name: string; source: "local" | "git" | "docs"; url: string; default_branch: string; branch?: string; indexed_sha: string | null; web_url?: string; visibility?: "org" | "restricted";
  status: "pending" | "indexing" | "ready" | "error"; file_count: number; chunk_count: number; updated_at: string;
}
export interface EvidenceItem {
  type: "code" | "commit" | "caller" | "doc" | "test" | "pr";
  number?: number; url?: string; state?: string; body?: string; jira_keys?: string[];
  title?: string; relation?: string;
  repo?: string; repo_id?: string; file?: string; symbol?: string; start_line?: number; end_line?: number;
  snippet?: string; sha?: string; message?: string; author?: string; committed_at?: string;
  diff_excerpt?: string; files?: { path: string; additions: number; deletions: number }[];
  caller?: string; provenance?: string;
}
export interface Hypothesis {
  id: string; rank: number; title: string; explanation: string; confidence: number;
  status: "unverified" | "supported" | "rejected"; evidence: EvidenceItem[];
}
export interface TimelineStep { step: string; agent: string; summary: string; duration_ms: number; at: string }
export interface InvestigationSummary {
  id: string; title: string; status: "queued" | "running" | "completed" | "failed"; confidence: number;
  root_cause: string; repo_ids: string[]; jira_issue_key: string | null; created_at: string; finished_at: string | null;
}
export interface Investigation extends InvestigationSummary {
  query: string; root_cause_status: "unconfirmed" | "confirmed" | "rejected"; suggested_fix: string;
  report_md: string; timeline: TimelineStep[]; hypotheses: Hypothesis[]; error: string | null; cache_hit: boolean;
  evidence: { memory?: { id: string; title: string; similarity: number; verified: boolean }[];
    callers?: { symbol: string; caller: string; file: string; repo: string; start_line: number }[];
    llm_used?: boolean; duration_s?: number };
  verification: { valid?: boolean; checked_evidence?: number; problems?: string[]; note?: string };
}
export interface Dashboard {
  repositories: number; indexed_repositories: number; investigations: number; completed: number;
  verified_accuracy: number | null; reviewed: number; avg_duration_s: number | null;
  recent: { id: string; title: string; status: string; confidence: number; created_at: string; repo: string }[];
  top_repositories: { name: string; count: number }[];
}
export interface UsageGroup {
  name: string; input_tokens: number; output_tokens: number; cached_input_tokens: number; cost: number;
  requests: number; errors: number; avg_latency_ms: number;
}
export interface UsageSummary {
  days: number; pricing_version: string; budgets: { daily: number; monthly: number };
  totals: { input_tokens: number; output_tokens: number; cost: number; requests: number; errors: number;
    cache_hit_rate: number; error_rate: number; tokens_avoided: number; cost_per_successful_rca: number | null;
    successful_investigations: number };
  vs_previous: { tokens: number | null; cost: number | null };
  by_model: UsageGroup[]; by_agent: UsageGroup[]; by_workflow: UsageGroup[];
  daily: { date: string; tokens: number; cost: number }[];
}
export interface SearchHit {
  repo_id: string; repo: string; file: string; symbol: string; parent: string; type: string; start_line: number;
  end_line: number; snippet: string; score: number; matched_terms: string[]; symbol_match: boolean;
}
export interface FileView {
  path: string; sha: string; language: string; content: string;
  symbols: { name: string; parent: string; type: string; start: number; end: number }[];
}
export interface JiraConnection { base_url: string; deployment: "cloud" | "datacenter"; email: string; allow_publish: boolean; default_project: string; account?: string }
export interface JiraIssue { key: string; summary: string; status: string; status_category?: string; type: string; priority: string; assignee?: string; reporter?: string; created?: string; updated?: string; labels?: string[]; components?: string[]; comments?: number; url: string; investigation_id: string | null }

const TOKEN_KEY = "repolens.token";
export const tokenStore = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t: string | null) => { try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = tokenStore.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.json !== undefined) { headers.set("Content-Type", "application/json"); init.body = JSON.stringify(init.json); }
  const res = await fetch(`/api${path}`, { ...init, headers });
  if (res.status === 401 && !path.startsWith("/auth/login")) onUnauthorized();
  if (!res.ok) {
    let msg = res.statusText;
    try { const b = await res.json(); msg = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail ?? b); } catch { /* not json */ }
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export interface GraphNode {
  id: string; type: "file" | "doc" | "section" | "function" | "class" | "commit" | "pr"; name: string; qualname: string;
  path: string; start_line: number; end_line: number; repo_id: string; repo: string; degree: number; meta: Record<string, unknown>;
}
export interface GraphEdge { id: string; source: string; target: string; rel: string; provenance: string; weight: number; evidence: string }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[] }
export interface Connection { rel: string; direction: "in" | "out"; label: string; provenance: string; weight: number; evidence: string; node: GraphNode }
export interface NodeDetail extends GraphData { node: GraphNode; content: string; connections: Connection[] }
export interface GraphStats { nodes: Record<string, number>; edges: { rel: string; provenance: string; count: number }[]; total_nodes: number; total_edges: number }
export interface KnowledgeHit {
  chunk_id: string; repo_id: string; repo: string; file: string; kind: "doc" | "code" | "pr"; chunk_type: string; title: string;
  symbol: string; start_line: number; end_line: number; content: string; score: number; node_id: string | null; url?: string;
  connections: Connection[]; via_graph: { from: string; relation: string; from_node_id: string | null } | null;
}
export interface AskResult {
  query: string; answer: string; mode: "llm" | "extractive" | "none"; cached?: boolean; unknown_citations?: string[];
  memory?: { similarity: number; asked: string; tokens_saved: number }; route?: { model: string; provider: string; tier: string; why: string; escalated: boolean };
  sources: { id: string; repo: string; repo_id?: string; url?: string; file: string; kind: string; title: string; start_line: number; end_line: number;
    node_id: string | null; snippet: string; via_graph: { from: string; relation: string } | null }[];
  connections: { from: string; label: string; to: string; to_type: string; provenance: string }[];
}
export interface DocRow { id: string; path: string; size: number; updated_at: string; source_type?: string; deletable?: boolean }

export interface LLMSettings {
  provider: "heuristic" | "anthropic" | "openai" | "github-models"; model: string; base_url: string; has_key: boolean; source: "org" | "env";
  input_price: number | null; output_price: number | null; offline: boolean; uses_github_token?: boolean; presets: Record<string, string[]>; embedding_provider: string;
}

export interface PullRow { number: number; title: string; state: "open" | "closed" | "merged"; author: string; url: string; files: number; commits: number; jira_keys: string[]; labels: string[]; merged_at: string | null; created_at: string }
export interface GithubConn { connected: boolean; api_base: string | null; auth_method?: "token" | "oauth" | null; login?: string | null; scope?: string | null; oauth_client_id?: string | null }
export interface DeviceStart { flow_id: string; user_code: string; verification_uri: string; expires_in: number; interval: number }
export interface DevicePoll { status: "pending" | "success" | "denied" | "expired" | "error"; interval?: number; error?: string }
export interface ModelInfo { id: string; name: string; publisher: string }
export interface IndexRunRow { id: string; status: string; commit_sha: string | null; files: number; chunks_written: number; chunks_reused: number; commits: number; secrets_redacted?: number; error: string | null; started_at: string; finished_at: string | null }

export interface RiskSignal { key: string; label: string; weight: number; value: number; points: number; detail: string; evidence: Record<string, unknown>[] }
export interface RiskDependent { node_id: string; type: string; name: string; path: string; repo: string; cross_repo: boolean; rel: string; target: string; evidence: string; provenance: string }
export interface RiskAssessment {
  id: string; repo_id: string; ref_type: "commit" | "pr" | "range" | "files"; ref: string; title: string; score: number;
  level: "low" | "medium" | "high" | "critical"; model_version: string; signals: RiskSignal[]; limitations: string[]; created_at: string;
  affected: { files: { path: string; additions: number; deletions: number }[]; symbols: { name: string; path: string; type: string }[]; dependents: RiskDependent[];
    tests_to_run: RiskDependent[]; docs_to_update: RiskDependent[]; symbol_level: boolean };
}
export interface RiskRow { id: string; ref_type: string; ref: string; title: string; score: number; level: RiskAssessment["level"]; created_at: string; top_signals: string[] }
export interface RiskScanRow { sha: string; title: string; author: string; committed_at: string; score: number; level: RiskAssessment["level"]; top_signals: string[] }

export interface AccessInfo { visibility: "org" | "restricted"; users: { id: string; email: string; name: string }[] }

// ---- DeepMind wiki
export interface WikiSource { repo_id: string; name: string; kind: "collection" | "repo"; origin: string; pages: number; visibility: string; status: string; last_synced_at: string | null; web_url: string }
export interface WikiHealthSummary { pages: number; orphans: number; broken_links: number; stale: number; duplicates: number; undocumented_code: number; outdated_references: number; documented_code_ratio: number | null; stale_days: number }
export interface WikiOverview { sources: WikiSource[]; totals: { sources: number; pages: number; sections: number }; health: WikiHealthSummary; concepts: number }
export interface WikiTreeNode { key: string; title: string; path: string | null; children: WikiTreeNode[] }
export interface PageRef { repo: string; repo_id: string; path: string; title: string; sections?: string[]; weight?: number }
export interface WikiPage {
  deletable?: boolean; managed_by?: string | null;
  repo_id: string; repo: string; path: string; title: string; markdown: string; unresolved_links: { link: string }[];
  breadcrumbs: { title: string; path: string | null }[]; outline: { title: string; anchor: string; line: number }[];
  source: { type: string; url: string; version: number | null; synced_at: string | null; updated_at: string | null; repo_url: string };
  backlinks: PageRef[]; links_out: PageRef[]; related: PageRef[]; concepts: string[];
  mentions_code: (GraphNode & { evidence: string; weight: number })[];
  insights?: PageInsights;
}
export interface PageInsights { kind: string; kind_label: string; purpose: string; commands: string[]; endpoints: string[]; settings: string[]; owners: { role: string; who: string }[]; warnings: { level: string; text: string }[]; steps: number; last_dated: string | null; words: number; reading_minutes: number }
export interface ConceptRow { term: string; docs: number; sections: number; code_like: boolean; in_code: boolean }
export interface ConceptDetail {
  term: string; summary: string; summary_source: { repo: string; path: string; section: string }; documents: number;
  definitions: { repo_id: string; repo: string; path: string; section: string; snippet: string }[];
  mentions: { repo_id: string; repo: string; path: string; section: string; snippet: string }[];
  code: GraphNode[]; related_terms: { term: string; count: number }[];
}
export interface WikiHealth {
  summary: WikiHealthSummary;
  orphans: PageRef[]; broken_links: (PageRef & { link: string; section?: string })[];
  stale: (PageRef & { age_days: number; last_updated: string })[];
  duplicates: { similarity: number; a: PageRef; b: PageRef }[];
  undocumented_code: GraphNode[]; outdated_references: { repo: string; repo_id: string; path: string; section: string; term: string }[];
}
export interface WikiSourceStatus { sync_interval_hours?: number; next_sync_at?: string | null; label?: string; id: string; repo_id: string; name: string; kind: string; url: string; mode: string; space_key: string; pages: number; status: string; repo_status: string; error: string | null; last_synced_at: string | null; visibility: string }
export interface ConfluenceConn { connected: boolean; base_url?: string; deployment?: "cloud" | "datacenter"; email?: string; account?: string }
export interface ConfluencePreview { kind: "page" | "space"; space_key: string; title: string; page_id: string; suggested_mode: "tree" | "space" }

export interface BranchList { branches: string[]; default: string }
export interface CheckoutRow { path: string; repos: string[]; exists: boolean; bytes: number; last_used_at: string | null; indexing: boolean; partial: boolean }
export interface StorageInfo { ttl_days: number; total_bytes: number; checkouts: CheckoutRow[] }
export interface CleanupResult { removed: { path: string; repos: string[]; bytes: number }[]; freed_bytes: number; dry_run: boolean }
export interface HeatResult { metric: string; label: string; max: number; values: Record<string, number>; raw: Record<string, number> }
export interface RetrievalStatus { embedding_state?: { stale: boolean; message: string }; embedding: { provider: string; model: string; dimensions: number; offline: boolean }; rerank: { provider: string; model: string; configured: boolean; depth: number } }
export interface OwnerRow { author: string; share: number; commits: number; lines: number; last_commit_at: string; files?: string[] }
export interface OwnershipArea { area: string; owners: OwnerRow[]; bus_factor: number; commits: number; last_activity: string; risk: "ok" | "single_owner" | "stale_owner" }
export interface OwnershipOverview { repo_id: string; repo: string; summary: { people: number; areas: number; bus_factor: number; areas_at_risk: number }; areas: OwnershipArea[]; people: { author: string; share: number; commits: number; last_commit_at: string; areas: string[] }[] }
export interface PathOwners { path: string; owners: OwnerRow[]; bus_factor: number }
export interface CiFailure { index: number; name: string; kind: string; message: string; details: string; file: string; line: number; count: number }
export interface CiPreview { format: string; failures: CiFailure[] }
export interface ConnectorField { key: string; label: string; type?: string; required: boolean; secret: boolean; placeholder?: string; help?: string; options?: string[] }
export interface ConnectorInfo { kind: string; label: string; description: string; needs_secret: boolean; fields: ConnectorField[] }
export interface DriftSymbol { id: string; name: string; qualname: string; path: string; type: string }
export interface DriftAlertRow { id: string; status: string; repo_id: string; repo: string; doc_repo_id: string; doc_repo: string; doc_path: string; doc_title: string; symbols: DriftSymbol[]; from_sha: string; to_sha: string; created_at: string; updated_at: string }
export interface ProposalRow { id: string; kind: "update" | "new"; source: string; source_ref: string; doc_repo_id: string; doc_repo: string; doc_path: string; title: string; rationale: string; drafted_by: string; status: string; result: string; created_at: string; decided_at: string | null }
export interface ProposalDetail extends ProposalRow { original: string; proposed: string; diff: string; apply_options: string[] }
export interface SsoSettings { configured: boolean; redirect_uri: string; issuer?: string; client_id?: string; has_secret?: boolean; allowed_domains?: string[]; default_role?: string; auto_provision?: boolean; enabled?: boolean }
export interface ScimStatus { configured: boolean; created_at: string | null; last_used_at: string | null }
export interface GhRepo { full_name: string; name: string; owner: string; description: string; private: boolean; fork: boolean; archived: boolean; default_branch: string; language: string; stars: number; open_issues: number; updated_at: string | null; html_url: string; clone_url: string; added_repo_id: string | null }
export interface GhRepoPage { total: number; page: number; per_page: number; truncated: boolean; items: GhRepo[] }
export interface GhPull { number: number; title: string; state: string; draft: boolean; author: string; head: string; base: string; labels: string[]; url: string; updated_at: string | null }
export interface GhIssue { number: number; title: string; state: string; comments: number; author: string; labels: string[]; url: string; updated_at: string | null }
export interface GhBranch { name: string; protected: boolean; default?: boolean }
export interface BitbucketConn { connected: boolean; deployment: "cloud" | "server" | null; base_url: string | null; username: string | null; workspaces?: string | null; account?: string | null }
export interface ModelList { provider: string; current: string; current_found: boolean; models: { id: string; name: string }[] }

export type Tier = "light" | "standard" | "strong";
export interface RegModel { id: string; model: string; tier: Tier; enabled: boolean; input_price: number | null; output_price: number | null }
export interface RegConnection { id: string; provider: string; base_url: string | null; has_key: boolean; legacy?: boolean; models: RegModel[] }
export interface PlanRow { kind: string; label: string; tier: Tier; model: string | null; provider: string | null; why: string }
export interface ModelRegistry { connections: RegConnection[]; savings_mode: boolean; plan: PlanRow[]; multi: boolean; active: number }
export interface MemoryStats { enabled: boolean; entries: number; hits: number; tokens_saved: number; threshold: number; ttl_hours: number }

export interface PageHit { repo: string; repo_id: string; path: string; title: string; snippet: string; start_line: number; score: number }
export interface PageSource { content: string; editable: boolean; reason: string | null; updated_at: string }
export interface AtlasSymbol { node_id: string; name: string; type: string; path: string; start_line: number; connections: number; documented: boolean }
export interface AtlasModule { module: string; files: number; functions: number; classes: number; languages: string[]; documented: number; coverage: number | null; top_symbols: AtlasSymbol[] }
export interface Atlas { modules: AtlasModule[]; summary: { modules: number; files: number; public_symbols: number; documented: number; coverage: number | null } }
export interface SimilarAnswer { id: string; asked: string; similarity: number; answer: string; sources: string[]; age_hours: number; hits: number }

export interface JiraComment { author: string; created: string; body: string }
export interface JiraDetail { key: string; summary: string; description: string; status: string; type: string; priority: string; labels: string[]; components: string[]; reporter: string; assignee: string; created: string; updated: string; url: string; comments: JiraComment[]; investigation_id: string | null }
export interface JiraClaim { by: string; text: string; comment: number }
export interface JiraAnalysis {
  key: string; headline: string; status: string; priority: string; reporter: string; assignee: string; participants: string[]; age_days: number | null; updated_days: number | null;
  signals: { errors: string[]; frames: { file: string; line: number; symbol: string; innermost?: boolean }[]; files: string[]; symbols: string[]; pull_requests: string[]; related_issues: string[] };
  discussion: { comments: number; cause: JiraClaim[]; tried: JiraClaim[]; workarounds: JiraClaim[]; fixes: JiraClaim[]; open_questions: JiraClaim[] };
  timeline: { author: string; created: string; kind: string; excerpt: string }[]; flags: string[]; search_terms: string[]; next_steps: string[];
  related: { repo: string; repo_id: string; file: string; kind: string; title: string; start_line: number; node_id: string | null }[];
}

export interface SessionRow { id: string; device: string; ip: string; method: string; created_at: string; last_seen_at: string; current: boolean }
export interface MetricsOverview {
  days: number;
  content: { repositories: number; doc_collections: number; pages: number; chunks: number; graph_nodes: number; graph_edges: number; indexing_errors: number };
  work: { investigations: number; completed: number; failed: number; confirmed: number; avg_minutes: number | null; proposals_pending: number; proposals_applied: number; drift_open: number };
  ai: { requests: number; tokens: number; cost: number; errors: number; cache_hits: number; tokens_avoided: number; savings_rate: number; by_model: UsageGroup[]; by_agent: UsageGroup[]; daily: { date: string; input_tokens: number; output_tokens: number; cost: number }[] };
  memory: { enabled: boolean; entries: number; hits: number; tokens_saved: number; stored: number };
  team: { users: number; active_sessions: number; active_users_period: number };
}

export interface BriefRef { id: string; name: string; qualname: string; type: string; path: string; repo: string }
export interface NodeBrief { summary: string; callers: BriefRef[]; dependencies: BriefRef[]; documented_in: BriefRef[]; history: BriefRef[]; callers_total: number; cross_repo: boolean; notes: string[] }

export interface MemoryEntry { id: string; asked: string; answer: string; sources: string[]; hits: number; tokens: number; tokens_saved: number; created_at: string; last_hit_at: string | null; kind: string; score?: number; matched?: "question" | "answer" }
export interface SkillFile { name: string; filename: string; markdown: string; entries: number }

export interface SysPoint { t: number; host_cpu: number | null; process_cpu: number | null; mem_used: number | null; mem_total: number | null; process_rss: number | null; rx_bps: number | null; tx_bps: number | null; rps: number; error_rate: number; p50_ms: number | null; p95_ms: number | null; container_limit: number | null }
export interface SysMetrics { available: boolean; cores: number | null; load_average: number[] | null; uptime_s: number; threads: number | null; current: SysPoint; history: SysPoint[]; interval_s: number }
export interface CalibrationBand { from: number; to: number; n: number; mean_confidence: number | null; confirmed_rate: number | null }
export interface Calibration { status: "empty" | "too_few" | "ok"; message: string; reviewed: number; min_for_curve: number; bands: CalibrationBand[]; confirmed_rate?: number; mean_confidence?: number; brier?: number; ece?: number; bias?: string }
export interface FeedbackSummary { days: number; total: number; helpful_rate: number | null; by_kind: Record<string, { up: number; down: number }>; from_memory: { total: number; helpful_rate: number | null } }

export interface OutboundItem { id: string; name: string; kind: string; host: string; stays_on_machine: boolean; sends: string[]; where: string; note: string }
export interface Outbound { items: OutboundItem[]; external: number; summary: string; local_only: string[] }

export interface AuditRow { id: string; at: string; actor: string; action: string; target: string; allowed: boolean; detail: Record<string, string> }
export interface AuditPage { total: number; items: AuditRow[] }
export interface AuditFacets { groups: string[]; users: { id: string; email: string }[]; denied: number }

export interface Person { id: string; email: string; name: string; role: "admin" | "member" | "viewer"; active: boolean; auth_source: string; totp_enabled: boolean; pending: "invite" | "reset" | null; created_at: string }
export interface TotpSetup { secret: string; uri: string }

export interface JiraShared { name: string; keys: string[] }
export interface JiraBatchTicket extends JiraAnalysis { summary: string; type: string; url: string; investigation_id: string | null }
export interface JiraBatch {
  tickets: JiraBatchTicket[];
  common: { errors: JiraShared[]; files: JiraShared[]; symbols: JiraShared[]; components: JiraShared[]; possible_duplicates: { keys: string[]; why: string; similarity: number }[]; statuses: Record<string, number>; priorities: Record<string, number>; attention: string[] };
  related: JiraAnalysis["related"];
}
export interface JiraInvestigateBatch { started: { issue_key: string; investigation_id: string }[]; existing: { issue_key: string; investigation_id: string }[] }

export interface DashAttention { severity: "high" | "medium" | "low" | "info"; text: string; to: string }
export interface DashInsights {
  attention: DashAttention[];
  investigations_per_day: { date: string; completed: number; failed: number }[];
  ai: { tokens: number; avoided: number; savings_rate: number; cost: number; requests: number; helpful_rate: number | null; ratings: number; daily: { date: string; tokens: number }[] };
  knowledge: { pages: number; chunks: number; graph_nodes: number; graph_edges: number; documented_ratio: number | null; wiki_issues: number; drift_open: number; proposals_pending: number };
  activity: { at: string; who: string; what: string; target: string }[];
  week: { started: number; failed: number; confirmed: number };
}

export interface AccessToken { id: string; name: string; prefix: string; scope: "read" | "write"; created_at: string; expires_at: string | null; last_used_at: string | null; revoked: boolean }
