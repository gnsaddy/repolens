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
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # false = deprovisioned: cannot sign in or use a token
    auth_source: Mapped[str] = mapped_column(String(10), default="password")  # password | sso | scim
    external_id: Mapped[str] = mapped_column(String(200), default="")  # the identity provider's subject / SCIM id
    totp_secret_encrypted: Mapped[str] = mapped_column(String(300), default="")  # app secret, pending until confirmed
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # last accepted 30-second step: a code works once
    recovery_codes: Mapped[list] = mapped_column(JSON, default=list)  # sha256 of unused one-time recovery codes


class FixProposal(Base):
    """A drafted code change for a confirmed root cause. Nothing is applied anywhere until a person approves it."""

    __tablename__ = "fix_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    repo_id: Mapped[str] = mapped_column(String(32), index=True)
    base_sha: Mapped[str] = mapped_column(String(64), default="")  # the indexed commit the edits were made against
    title: Mapped[str] = mapped_column(String(300))
    rationale: Mapped[str] = mapped_column(Text, default="")
    edits: Mapped[list] = mapped_column(JSON, default=list)  # [{path, old, new}] exact-text replacements
    diff: Mapped[str] = mapped_column(Text, default="")
    checks: Mapped[dict] = mapped_column(JSON, default=dict)  # {syntax_ok, files, lines_changed, notes}
    tests: Mapped[list] = mapped_column(JSON, default=list)  # tests to run or add, as advice
    drafted_by: Mapped[str] = mapped_column(String(10), default="template")  # llm | template
    model: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending|approved|rejected|failed
    result: Mapped[str] = mapped_column(Text, default="")
    pr_url: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatThread(Base):
    """A person's conversation with the knowledge base. Private to its owner: not even admins can read it."""

    __tablename__ = "chat_threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    repo_ids: Mapped[list] = mapped_column(JSON, default=list)  # empty = everything this person may see
    fallback: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # may answer from general knowledge when the sources do not
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", order_by="ChatMessage.created_at, ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list] = mapped_column(JSON, default=list)  # what an answer cited, as it was when it was written
    connections: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(20), default="")  # llm | extractive | general | none
    attachment: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {"name", "chars"} of a .txt or .log file sent with a question
    attachment_text: Mapped[str] = mapped_column(Text, default="")  # kept so an edited question can be answered again
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thread: Mapped[ChatThread] = relationship(back_populates="messages")


class ApiToken(Base):
    """A personal access token for scripts, CI and editor tools (MCP). Only a hash is stored; it acts as its owner with
    reduced power: `read` tokens are viewers, `write` tokens are at most members, never admins."""

    __tablename__ = "api_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(12))  # the first characters, so a person can recognise their token
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(8), default="read")  # read | write
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthToken(Base):
    """A one-time link to set a password: an invitation for a new person, or a reset an admin hands to someone."""

    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # invite | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # only a hash is stored
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeedbackEvent(Base):
    """A person's verdict on an answer or an investigation. Feeds the quality trend and the evaluation question sets."""

    __tablename__ = "feedback_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # answer | memory | investigation
    target_id: Mapped[str] = mapped_column(String(64), default="")
    rating: Mapped[str] = mapped_column(String(8))  # up | down
    query: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # mode, model, from-memory flag, source files
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class UserSession(Base):
    """One sign-in. Tokens carry the session id, so a session can be ended from the server at any time."""

    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[str] = mapped_column(String(10), default="password")  # password | sso
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    account: Mapped[str] = mapped_column(String(200), default="")  # who the credential belongs to, for display
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
    savings_mode: Mapped[bool] = mapped_column(Boolean, default=True)  # route simple tasks to cheaper models
    independent_review: Mapped[bool] = mapped_column(Boolean, default=False)  # a second, evidence-only look in each RCA


class LLMConnection(Base):
    """One provider account (a key) in the multi-model registry. The key is Fernet-encrypted and never returned."""

    __tablename__ = "llm_connections"
    __table_args__ = (UniqueConstraint("org_id", "provider", "base_url"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # anthropic | openai | gemini
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LLMModel(Base):
    """A model enabled for routing, with the tier it serves: light (cheap, fast), standard, strong (hard problems)."""

    __tablename__ = "llm_models"
    __table_args__ = (UniqueConstraint("connection_id", "model"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("llm_connections.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    tier: Mapped[str] = mapped_column(String(10), default="standard")  # light | standard | strong
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    input_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # USD per 1M tokens
    output_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AnswerMemory(Base):
    """Answers the model already gave, kept locally so a repeated or re-worded question costs no tokens."""

    __tablename__ = "answer_memory"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="ask", index=True)
    scope_key: Mapped[str] = mapped_column(String(64), index=True)  # who could see what when it was answered
    query: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(EmbeddingType, nullable=True)
    answer: Mapped[dict] = mapped_column(JSON, default=dict)
    source_state: Mapped[dict] = mapped_column(JSON, default=dict)  # {repo_id: indexed_sha} the answer relied on
    tokens: Mapped[int] = mapped_column(Integer, default=0)  # input + output tokens this answer cost once
    hits: Mapped[int] = mapped_column(Integer, default=0)
    tokens_saved: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    account: Mapped[str] = mapped_column(String(200), default="")  # who the credential belongs to, for display
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


class DriftAlert(Base):
    """Code that a document describes has changed since the document was written. One open alert per page and repo."""

    __tablename__ = "drift_alerts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)  # code repo
    doc_repo_id: Mapped[str] = mapped_column(String(32), index=True)
    doc_path: Mapped[str] = mapped_column(String(1000))
    doc_title: Mapped[str] = mapped_column(String(500), default="")
    symbols: Mapped[list] = mapped_column(JSON, default=list)  # [{id, name, qualname, path, type}]
    from_sha: Mapped[str] = mapped_column(String(64), default="")
    to_sha: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open | dismissed | resolved
    doc_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(32), default="")


class DocProposal(Base):
    """A suggested wiki change (or new page) awaiting a human decision. Nothing is written before approval."""

    __tablename__ = "doc_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="update")  # update | new
    source: Mapped[str] = mapped_column(String(12), default="manual")  # drift | incident | manual
    source_ref: Mapped[str] = mapped_column(String(32), default="")  # drift alert id / investigation id
    doc_repo_id: Mapped[str] = mapped_column(
        String(32), default="", index=True
    )  # "" for a new page in a new collection
    doc_path: Mapped[str] = mapped_column(String(1000), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    original: Mapped[str] = mapped_column(Text, default="")
    proposed: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    base_hash: Mapped[str] = mapped_column(String(64), default="")  # hash of the page when drafted: detects edits since
    drafted_by: Mapped[str] = mapped_column(String(12), default="template")  # template | llm
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|applied|rejected|failed
    result: Mapped[str] = mapped_column(Text, default="")  # where it was applied, or why it failed
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_by: Mapped[str] = mapped_column(String(32), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SsoConfig(Base):
    """OpenID Connect single sign-on for one organisation (authorization code flow with PKCE)."""

    __tablename__ = "sso_configs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), unique=True)
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(300))
    client_secret_encrypted: Mapped[str] = mapped_column(Text, default="")  # never returned by the API
    allowed_domains: Mapped[list] = mapped_column(JSON, default=list)  # e-mail domains that may sign in this way
    default_role: Mapped[str] = mapped_column(String(20), default="viewer")
    auto_provision: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SsoFlow(Base):
    """One in-progress sign-in: state, nonce and PKCE verifier live here, never in the browser. Single use."""

    __tablename__ = "sso_flows"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # the OAuth `state`
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    login_code: Mapped[str] = mapped_column(String(64), default="", index=True)  # one-time code handed to the SPA
    user_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ScimToken(Base):
    """Bearer token the identity provider uses to provision users. Only a hash is stored."""

    __tablename__ = "scim_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BitbucketConnection(Base):
    """Bitbucket Cloud (app password or access token) or Bitbucket Server / Data Center (personal access token)."""

    __tablename__ = "bitbucket_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), unique=True)
    deployment: Mapped[str] = mapped_column(String(10), default="cloud")  # cloud | server
    base_url: Mapped[str] = mapped_column(String(300), default="")  # server only; cloud is api.bitbucket.org
    username: Mapped[str] = mapped_column(String(200), default="")  # cloud app password user; empty for token auth
    workspaces: Mapped[str] = mapped_column(String(300), default="")  # cloud: comma-separated workspace slugs to list
    account: Mapped[str] = mapped_column(String(200), default="")  # who the credential belongs to, for display
    token_encrypted: Mapped[str] = mapped_column(Text)  # never returned by the API
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EvalRun(Base):
    """A 'measure it on my repository' run: fix commits from the repo's own history become test questions."""

    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | completed | failed
    cases: Mapped[int] = mapped_column(Integer, default=10)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Skill(Base):
    """Guidance an organisation adds for the model: how its APIs are written, how its runbooks look, how it debugs.

    Private to one organisation. A member can submit one; it applies only once an admin has approved it."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("org_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))  # short slug, unique per organisation
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")  # when it applies: matched against the question
    body: Mapped[str] = mapped_column(Text, default="")  # the instructions
    tasks: Mapped[list] = mapped_column(JSON, default=list)  # lookup | explain | build | investigate; empty = any
    repo_ids: Mapped[list] = mapped_column(JSON, default=list)  # empty = every repository
    always: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # apply to every matching task, not only on keyword match
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | disabled
    note: Mapped[str] = mapped_column(Text, default="")  # why it was rejected
    created_by: Mapped[str] = mapped_column(String(32), default="")
    reviewed_by: Mapped[str] = mapped_column(String(32), default="")
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GoldenQuestion(Base):
    """A question a workspace expects to be answered well, with what a good answer must contain."""

    __tablename__ = "golden_questions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    expect_files: Mapped[list] = mapped_column(JSON, default=list)  # path fragments that must appear among the sources
    expect_phrases: Mapped[list] = mapped_column(JSON, default=list)  # words the written answer must contain
    repo_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GoldenRun(Base):
    """One pass over the saved questions, so quality can be compared before and after a change."""

    __tablename__ = "golden_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), default="")
    mode: Mapped[str] = mapped_column(String(20), default="retrieval")  # retrieval (free) | full (uses the model)
    trigger: Mapped[str] = mapped_column(
        String(20), default="manual"
    )  # manual | reindex (started by an index finishing)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | completed | failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list] = mapped_column(JSON, default=list)
    fingerprint: Mapped[dict] = mapped_column(JSON, default=dict)  # what the answers depended on when this ran
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RepoGroup(Base):
    """A saved set of repositories analysed as one system: how they depend on each other, change together, and fit."""

    __tablename__ = "repo_groups"
    __table_args__ = (UniqueConstraint("org_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    repo_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GroupOverview(Base):
    """A written overview of a group, shared with the workspace and signed with who asked for it."""

    __tablename__ = "group_overviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("repo_groups.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(12), default="extractive")  # llm | extractive
    model: Mapped[str] = mapped_column(String(100), default="")
    repo_ids: Mapped[list] = mapped_column(
        JSON, default=list
    )  # what it was built from; shown only to those who see all
    unsupported_names: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RepoLink(Base):
    """One proved dependency between two repositories: a function, class or file in one that uses one in the other."""

    __tablename__ = "repo_links"
    __table_args__ = (UniqueConstraint("org_id", "kind", "src_node_id", "dst_node_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # import | http
    rel: Mapped[str] = mapped_column(String(12))  # USES | IMPORTS | HTTP
    tier: Mapped[str] = mapped_column(String(12))  # imported (the file names it) | used (a function calls it)
    trust: Mapped[str] = mapped_column(String(12))  # fact | inferred | uncertain
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    src_repo_id: Mapped[str] = mapped_column(String(32), index=True)
    dst_repo_id: Mapped[str] = mapped_column(String(32), index=True)
    src_node_id: Mapped[str] = mapped_column(String(32), index=True)
    dst_node_id: Mapped[str] = mapped_column(String(32), index=True)
    src_label: Mapped[str] = mapped_column(
        String(600), default=""
    )  # path:name, kept so a removed link can still be shown
    dst_label: Mapped[str] = mapped_column(String(600), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")  # the proof: the import line, or the URL and the route
    more: Mapped[int] = mapped_column(Integer, default=0)  # further callers of the same target that were not stored
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LinkState(Base):
    """When a workspace's links were last worked out, from which index versions, and what could be read."""

    __tablename__ = "link_states"
    org_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    baseline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )  # links before this are not "new"
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)


class ArchRule(Base):
    """An architecture rule of a saved system: one repository must not depend on another."""

    __tablename__ = "arch_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("repo_groups.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="forbid")
    src_repo: Mapped[str] = mapped_column(String(32))
    dst_repo: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
