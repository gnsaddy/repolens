from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .config import get_settings
from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class EmbeddingType(TypeDecorator):
    """pgvector `vector(n)` on PostgreSQL, JSON list elsewhere (tests / local SQLite)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(get_settings().embedding_dim))
        return dialect.type_descriptor(JSON())


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin | member | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("org_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="local")  # local | git
    url: Mapped[str] = mapped_column(String(1000), default="")  # clone URL or local path
    local_path: Mapped[str] = mapped_column(String(1000), default="")
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    branch: Mapped[str] = mapped_column(String(200), default="")  # "" = the remote's default branch
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visibility: Mapped[str] = mapped_column(String(12), default="org")  # org | restricted
    web_url: Mapped[str] = mapped_column(String(500), default="")  # https://host/owner/name for evidence links
    indexed_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|indexing|ready|error
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RepoAccess(Base):
    """Grants a user access to a *restricted* repository (admins always have access)."""

    __tablename__ = "repo_access"
    __table_args__ = (UniqueConstraint("repo_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IndexRun(Base):
    __tablename__ = "index_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    files_indexed: Mapped[int] = mapped_column(Integer, default=0)
    chunks_written: Mapped[int] = mapped_column(Integer, default=0)
    chunks_reused: Mapped[int] = mapped_column(Integer, default=0)
    commits_indexed: Mapped[int] = mapped_column(Integer, default=0)
    secrets_redacted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CodeChunk(Base):
    __tablename__ = "code_chunks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(String(1000), index=True)
    symbol_name: Mapped[str] = mapped_column(String(500), default="")
    parent_symbol: Mapped[str] = mapped_column(String(500), default="")
    language: Mapped[str] = mapped_column(String(40), default="")
    chunk_type: Mapped[str] = mapped_column(String(40), default="block")
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    imports: Mapped[list] = mapped_column(JSON, default=list)
    calls: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)


class Change(Base):
    """Commits (and PR metadata when known)."""

    __tablename__ = "changes"
    __table_args__ = (UniqueConstraint("repo_id", "sha"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    sha: Mapped[str] = mapped_column(String(64), index=True)
    parent_sha: Mapped[str] = mapped_column(String(64), default="")
    author: Mapped[str] = mapped_column(String(300), default="")
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{path, additions, deletions}]
    diff_excerpt: Mapped[str] = mapped_column(Text, default="")
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_title: Mapped[str] = mapped_column(String(500), default="")
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(500))
    query: Mapped[str] = mapped_column(Text)
    repo_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    root_cause: Mapped[str] = mapped_column(Text, default="")
    root_cause_status: Mapped[str] = mapped_column(String(30), default="unconfirmed")
    suggested_fix: Mapped[str] = mapped_column(Text, default="")
    report_md: Mapped[str] = mapped_column(Text, default="")
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    jira_issue_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hypotheses: Mapped[list[Hypothesis]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", order_by="Hypothesis.rank"
    )


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(500))
    explanation: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="unverified")  # unverified|supported|rejected
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    investigation: Mapped[Investigation] = relationship(back_populates="hypotheses")


class LLMCallEvent(Base):
    __tablename__ = "llm_call_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    workflow: Mapped[str] = mapped_column(String(50), default="rca")
    workflow_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    agent_name: Mapped[str] = mapped_column(String(50), default="")
    kind: Mapped[str] = mapped_column(String(20), default="chat")  # chat | embedding
    provider: Mapped[str] = mapped_column(String(50))
    model_name: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_avoided: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    pricing_version: Mapped[str] = mapped_column(String(30), default="")
    usage_source: Mapped[str] = mapped_column(String(30), default="provider_reported")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    jira_issue_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class MemoryEntry(Base):
    __tablename__ = "memory_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # incident | repository
    repo_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_id: Mapped[str] = mapped_column(String(32), default="")  # e.g. investigation id
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # repositories whose content this memory was derived from; NULL (legacy) = visible to unrestricted users only
    scope_repo_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    task: Mapped[str] = mapped_column(String(50))
    value: Mapped[dict] = mapped_column(JSON)
    tokens_baseline: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JiraConnection(Base):
    __tablename__ = "jira_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    deployment: Mapped[str] = mapped_column(String(20), default="cloud")  # cloud | datacenter
    email: Mapped[str] = mapped_column(String(320), default="")
    secret_encrypted: Mapped[str] = mapped_column(Text)  # Fernet token; never returned by the API
    allow_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    default_project: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JiraIssueLink(Base):
    __tablename__ = "jira_issue_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    issue_key: Mapped[str] = mapped_column(String(50), index=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JiraPublishEvent(Base):
    __tablename__ = "jira_publish_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    investigation_id: Mapped[str] = mapped_column(String(32), index=True)
    issue_key: Mapped[str] = mapped_column(String(50))
    approved_by: Mapped[str] = mapped_column(String(32))
    remote_comment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # published | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    actor_id: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(100), index=True)
    target: Mapped[str] = mapped_column(String(200), default="")
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Document(Base):
    """Full text of a document in an uploaded docs collection (Repository.source == 'docs')."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("repo_id", "path"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    # where the page came from: upload | paste | url | confluence
    source_type: Mapped[str] = mapped_column(String(20), default="upload")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    source_id: Mapped[str] = mapped_column(String(100), default="", index=True)  # e.g. Confluence page id
    source_version: Mapped[int] = mapped_column(Integer, default=0)
    parent_path: Mapped[str] = mapped_column(String(1000), default="")  # page hierarchy (Confluence ancestors)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KGNode(Base):
    """Knowledge-graph node. IDs are deterministic so re-indexing keeps edges from other repos valid."""

    __tablename__ = "kg_nodes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(String(32), index=True)
    type: Mapped[str] = mapped_column(String(20), index=True)  # file|doc|section|function|method|class|commit|pr
    name: Mapped[str] = mapped_column(String(500))
    qualname: Mapped[str] = mapped_column(String(800), default="")
    path: Mapped[str] = mapped_column(String(1000), default="")
    start_line: Mapped[int] = mapped_column(Integer, default=0)
    end_line: Mapped[int] = mapped_column(Integer, default=0)
    chunk_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    degree: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class KGEdge(Base):
    __tablename__ = "kg_edges"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(String(32), index=True)  # repository that owns (derived) the edge
    src_id: Mapped[str] = mapped_column(String(32), index=True)
    dst_id: Mapped[str] = mapped_column(String(32), index=True)
    rel: Mapped[str] = mapped_column(String(20), index=True)
    # provenance keeps facts apart from inference: static_analysis | doc_link | doc_mention | git | semantic | github
    provenance: Mapped[str] = mapped_column(String(20))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[str] = mapped_column(String(400), default="")


class LLMSetting(Base):
    """Per-organisation model provider configuration. The API key is Fernet-encrypted and never returned."""

    __tablename__ = "llm_settings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(20), default="heuristic")  # heuristic | anthropic | openai
    model: Mapped[str] = mapped_column(String(100), default="")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    input_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # USD per 1M tokens
    output_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GithubConnection(Base):
    __tablename__ = "github_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), unique=True)
    api_base: Mapped[str] = mapped_column(String(300), default="https://api.github.com")
    token_encrypted: Mapped[str] = mapped_column(Text)
    auth_method: Mapped[str] = mapped_column(String(10), default="token")  # token | oauth
    client_id: Mapped[str] = mapped_column(String(100), default="")  # OAuth App client id (public)
    login: Mapped[str] = mapped_column(String(100), default="")
    scope: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class OAuthFlow(Base):
    """A pending GitHub device-flow authorisation. The device code is a secret and stays server-side."""

    __tablename__ = "oauth_flows"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32))
    client_id: Mapped[str] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(200), default="")
    device_code_encrypted: Mapped[str] = mapped_column(Text)
    api_base: Mapped[str] = mapped_column(String(300), default="https://api.github.com")
    interval_s: Mapped[int] = mapped_column(Integer, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repo_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(20), default="open")  # open | closed | merged
    author: Mapped[str] = mapped_column(String(200), default="")
    base_ref: Mapped[str] = mapped_column(String(200), default="")
    head_ref: Mapped[str] = mapped_column(String(200), default="")
    merge_commit_sha: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{path, additions, deletions}]
    commit_shas: Mapped[list] = mapped_column(JSON, default=list)
    comments: Mapped[list] = mapped_column(JSON, default=list)  # [{author, body, path}]
    jira_keys: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RiskAssessment(Base):
    """Change-risk assessment (Oracle). Interpretable: every score carries its signals, evidence and limits."""

    __tablename__ = "risk_assessments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    ref_type: Mapped[str] = mapped_column(String(10))  # commit | pr | range | files
    ref: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500), default="")
    score: Mapped[float] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(10))
    model_version: Mapped[str] = mapped_column(String(40))
    signals: Mapped[list] = mapped_column(JSON, default=list)
    affected: Mapped[dict] = mapped_column(JSON, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ConfluenceConnection(Base):
    """Organisation-level Confluence credentials. The secret is Fernet-encrypted and never returned."""

    __tablename__ = "confluence_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    deployment: Mapped[str] = mapped_column(String(12), default="cloud")  # cloud | datacenter
    email: Mapped[str] = mapped_column(String(320), default="")
    secret_encrypted: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WikiSource(Base):
    """A re-syncable import into a docs collection (currently Confluence)."""

    __tablename__ = "wiki_sources"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="confluence")
    url: Mapped[str] = mapped_column(String(1000))
    mode: Mapped[str] = mapped_column(String(10), default="tree")  # page | tree | space
    space_key: Mapped[str] = mapped_column(String(100), default="")
    root_page_id: Mapped[str] = mapped_column(String(100), default="")
    max_pages: Mapped[int] = mapped_column(Integer, default=300)
    pages_total: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret settings of a connector (jql, channels...)
    secret_encrypted: Mapped[str] = mapped_column(Text, default="")  # Fernet token; never returned by the API
    sync_interval_hours: Mapped[int] = mapped_column(Integer, default=0)  # 0 = manual only
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="never")  # never | running | completed | failed
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
