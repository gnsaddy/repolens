from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)
    name: str = ""
    organization: str = "My Organization"


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(ORM):
    id: str
    email: str
    name: str
    role: str
    org_id: str
    active: bool = True
    auth_source: str = "password"


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)
    name: str = ""
    role: str = Field(default="member", pattern="^(admin|member|viewer)$")


class RepoCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[\w.\- ]+$")
    source: str = Field(default="local", pattern="^(local|git)$")
    url: str = Field(min_length=1, max_length=1000)
    branches: list[str] = Field(default_factory=list, max_length=20)  # one index per branch; empty = default branch


class StorageCleanup(BaseModel):
    older_than_days: int = Field(default=30, ge=0, le=3650)
    dry_run: bool = False


class RepoOut(ORM):
    id: str
    name: str
    source: str
    url: str
    web_url: str = ""
    visibility: str = "org"
    default_branch: str
    branch: str = ""
    indexed_sha: str | None
    status: str
    file_count: int
    chunk_count: int
    updated_at: datetime

    @field_validator("url")
    @classmethod
    def _no_credentials(cls, v: str) -> str:  # legacy rows may hold https://user:token@host/...
        from .services.links import strip_credentials

        return strip_credentials(v)


class CiLogIn(BaseModel):
    log: str = Field(min_length=1, max_length=2_000_000)


class CiStartIn(CiLogIn):
    repo_ids: list[str] = []
    indexes: list[int] = Field(min_length=1, max_length=10)


class InvestigationCreate(BaseModel):
    query: str = Field(min_length=5, max_length=20000)
    repo_ids: list[str] = []
    title: str | None = Field(default=None, max_length=300)


class HypothesisOut(ORM):
    id: str
    rank: int
    title: str
    explanation: str
    confidence: float
    status: str
    evidence: list


class InvestigationSummary(ORM):
    id: str
    title: str
    status: str
    confidence: float
    root_cause: str
    repo_ids: list
    jira_issue_key: str | None
    created_at: datetime
    finished_at: datetime | None


class InvestigationOut(InvestigationSummary):
    query: str
    root_cause_status: str
    suggested_fix: str
    report_md: str
    timeline: list
    evidence: dict
    verification: dict
    error: str | None
    cache_hit: bool
    hypotheses: list[HypothesisOut]


class ReviewIn(BaseModel):
    outcome: str = Field(pattern="^(confirmed|rejected)$")
    note: str = ""


class JiraConnectionIn(BaseModel):
    base_url: str
    deployment: str = Field(default="cloud", pattern="^(cloud|datacenter)$")
    email: str = ""
    token: str = Field(min_length=4)
    allow_publish: bool = False
    default_project: str = ""


class JiraConnectionOut(ORM):
    base_url: str
    deployment: str
    email: str
    allow_publish: bool
    default_project: str


class JiraInvestigateIn(BaseModel):
    issue_key: str
    repo_ids: list[str] = []


class JiraPublishIn(BaseModel):
    investigation_id: str
    issue_key: str | None = None
    approved: bool = False


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[\w.\- ]+$")


class DocIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=1_000_000)


class DocsUpload(BaseModel):
    documents: list[DocIn] = Field(min_length=1, max_length=200)


class HeatIn(BaseModel):
    metric: str = Field(pattern="^(churn|bugs|connections|undocumented)$")
    node_ids: list[str] = Field(min_length=1, max_length=500)
    days: int = Field(default=90, ge=1, le=3650)


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    repo_ids: list[str] = []
    k: int = Field(default=10, ge=1, le=30)
    kinds: list[str] = Field(default_factory=list)


class LLMSettingIn(BaseModel):
    provider: str = Field(pattern="^(heuristic|anthropic|openai|github-models)$")
    model: str = Field(default="", max_length=100, pattern=r"^[\w.:/\-]*$")
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    input_price: float | None = Field(default=None, ge=0, le=10_000)
    output_price: float | None = Field(default=None, ge=0, le=10_000)


class GithubConnectionIn(BaseModel):
    token: str = Field(min_length=8, max_length=500)
    api_base: str = Field(default="https://api.github.com", max_length=300)


class SyncPullsIn(BaseModel):
    github_repo: str | None = Field(default=None, max_length=200, pattern=r"^[\w.-]+/[\w.-]+$")
    limit: int = Field(default=100, ge=1, le=300)


class OAuthStartIn(BaseModel):
    client_id: str = Field(default="", max_length=100, pattern=r"^[\w.-]*$")
    scope: str = Field(default="repo read:user", max_length=200, pattern=r"^[\w:, -]*$")
    api_base: str = Field(default="https://api.github.com", max_length=300)


class OAuthPollIn(BaseModel):
    flow_id: str = Field(min_length=8, max_length=64)


class RiskFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    additions: int = Field(default=0, ge=0, le=10_000_000)
    deletions: int = Field(default=0, ge=0, le=10_000_000)


class RiskAssessIn(BaseModel):
    """Exactly one of: sha | pr_number | base+head | files."""

    repo_id: str
    sha: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{4,64}$")
    pr_number: int | None = Field(default=None, ge=1)
    base: str | None = Field(default=None, max_length=120)
    head: str | None = Field(default=None, max_length=120)
    files: list[RiskFile] | None = Field(default=None, max_length=500)
    title: str = Field(default="", max_length=500)


class AccessIn(BaseModel):
    visibility: str = Field(pattern="^(org|restricted)$")
    user_ids: list[str] = Field(default_factory=list, max_length=500)


class ConfluenceConnectionIn(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    deployment: str = Field(default="cloud", pattern="^(cloud|datacenter)$")
    email: str = Field(default="", max_length=320)
    token: str = Field(min_length=4, max_length=500)


class ConfluencePreviewIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000)


class ConfluenceImportIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    mode: str = Field(default="auto", pattern="^(auto|page|tree|space)$")
    name: str = Field(default="", max_length=200, pattern=r"^[\w.\-: ]*$")
    max_pages: int = Field(default=300, ge=1, le=2000)
    visibility: str = Field(default="restricted", pattern="^(org|restricted)$")


class ImportUrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    collection_id: str | None = Field(default=None, max_length=64)
    name: str = Field(default="", max_length=200, pattern=r"^[\w.\-: ]*$")


class ConnectorCheckIn(BaseModel):
    config: dict = Field(default_factory=dict)
    secret: str = Field(default="", max_length=20000)


class ConnectorImportIn(ConnectorCheckIn):
    name: str = Field(default="", max_length=200, pattern=r"^[\w.\-: #@]*$")
    visibility: str = Field(default="restricted", pattern="^(org|restricted)$")
    sync_interval_hours: int = Field(default=0, ge=0, le=720)
    max_docs: int = Field(default=500, ge=1, le=2000)


class ScheduleIn(BaseModel):
    sync_interval_hours: int = Field(ge=0, le=720)


class SlackExportIn(BaseModel):
    files: list[DocIn] = Field(min_length=1, max_length=400)  # path like "general/2026-01-31.json"
    collection_id: str | None = Field(default=None, max_length=64)
    name: str = Field(default="", max_length=200, pattern=r"^[\w.\-: ]*$")


class ProposalEdit(BaseModel):
    proposed: str = Field(min_length=1, max_length=200_000)


class ProposalDecision(BaseModel):
    approve: bool
    target: str = Field(default="auto", pattern="^(auto|local|confluence_comment)$")


class RunbookIn(BaseModel):
    investigation_id: str = Field(min_length=1, max_length=64)
    collection_id: str = Field(default="", max_length=64)


class WikiAskIn(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    repo_ids: list[str] = Field(default_factory=list, max_length=100)
    k: int = Field(default=8, ge=1, le=10)
