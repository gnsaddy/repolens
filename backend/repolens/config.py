from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPOLENS_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "sqlite:///./repolens.db"
    # Auth
    jwt_secret: str = "dev-insecure-change-me"
    jwt_ttl_minutes: int = 60 * 12
    session_idle_minutes: int = 60 * 8  # a session unused this long is ended (0 = never)
    login_max_failures: int = 5  # wrong passwords per account and address before pausing
    login_window_seconds: int = 600
    allow_registration: bool = True
    # Externally visible base URL (https://repolens.example.com), used for the SSO redirect URI.
    public_url: str = ""
    # Secrets at rest (Fernet key, urlsafe base64 32 bytes). Derived from jwt_secret when empty (dev only).
    secret_key: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Repositories
    repos_dir: str = "./data/repos"
    max_file_bytes: int = 400_000
    # Git clones are blob-less ("blob:none") and have no working tree: history is complete, file contents are fetched
    # in one batch when indexing. Set to "" for a full clone.
    clone_filter: str = "blob:none"
    max_branches: int = 8
    # Checkouts of git repositories unused for this many days are deleted (re-cloned on the next index). 0 disables.
    checkout_ttl_days: int = 30
    # Local (server-side path) repositories are only allowed under these roots outside dev
    local_repo_roots: list[str] = Field(default_factory=list)
    # Embeddings: "hash" (local, offline) or "openai" (OpenAI-compatible endpoint)
    embedding_provider: str = "hash"
    embedding_dim: int = 256
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_send_dimensions: bool = True  # some providers (e.g. Voyage) reject the OpenAI `dimensions` field
    # Answer memory: re-worded repeats of a question are answered from what the model already said (no tokens)
    answer_memory_enabled: bool = True
    answer_memory_threshold: float = 0.92  # cosine similarity of the two questions
    answer_memory_word_threshold: float = 0.8  # overlap of the questions' content words
    answer_memory_ttl_hours: int = 72
    answer_memory_max_entries: int = 2000
    # Second-stage reranking of search results: "none" | "lexical" (offline) | "api" (Cohere/Jina/Voyage-style /rerank)
    rerank_provider: str = "lexical"
    rerank_base_url: str = ""
    rerank_model: str = "rerank-v3.5"
    rerank_api_key: str = ""
    rerank_timeout_s: float = 10.0
    rerank_depth: int = 30  # candidates re-scored per query
    # Vector hits below this cosine similarity are ignored (raise to ~0.3 for hosted embedding models)
    vector_min_similarity: float = 0.15
    # LLM: "heuristic" (offline, no external calls), "anthropic", "openai" (OpenAI-compatible)
    llm_provider: str = "heuristic"
    llm_model: str = "claude-sonnet-5-5"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    # Knowledge graph
    kg_semantic_threshold: float = 0.30  # min cosine for RELATED_TO edges
    kg_semantic_top_k: int = 3
    # Budgets (USD); 0 disables
    daily_budget_usd: float = 0.0
    monthly_budget_usd: float = 0.0
    # Pricing per 1M tokens, versioned: {"model": {"input": x, "output": y, "cached_input": z}}
    pricing_version: str = "2026-10-01"
    pricing_json: str = ""
    # Investigation limits
    max_investigation_loops: int = 2
    run_jobs_inline: bool = False  # tests: run background jobs synchronously
    # Telemetry
    otlp_endpoint: str = ""
    service_name: str = "repolens-api"
    # GitHub App (optional)
    github_webhook_secret: str = ""
    # OAuth App client id used for the "Connect with GitHub" device flow (public value; no secret needed)
    github_client_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
