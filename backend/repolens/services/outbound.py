"""An honest list of every place this server sends data, read from what is configured right now.

Nothing here is a setting: it describes the connections admins have made so people can see, and decide about, what
leaves the server. The local features (search, graph, memory, wiki, evals) never appear because they send nothing."""

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    BitbucketConnection,
    ConfluenceConnection,
    GithubConnection,
    JiraConnection,
    LLMConnection,
    LLMModel,
    SsoConfig,
    WikiSource,
)
from . import llm_router

PROVIDER_HOST = {
    "anthropic": "api.anthropic.com",
    "gemini": "generativelanguage.googleapis.com",
    "openai": "api.openai.com",
}


def _host(url: str, default: str = "") -> str:
    return urlparse(url).netloc or default


def _local(host: str) -> bool:
    h = host.split(":")[0]
    return h in ("localhost", "127.0.0.1", "::1", "host.docker.internal") or h.endswith(".local")


def _item(id_: str, name: str, kind: str, host: str, sends: list[str], where: str, extra: str = "") -> dict:
    local = _local(host) if host else False
    return {
        "id": id_,
        "name": name,
        "kind": kind,  # model | embedding | rerank | connector | telemetry | identity
        "host": host,
        "stays_on_machine": local,
        "sends": sends,
        "where": where,
        "note": extra,
    }


def list_outbound(db: Session, org_id: str) -> dict:
    s = get_settings()
    items: list[dict] = []

    # language models
    conns = list(db.scalars(select(LLMConnection).where(LLMConnection.org_id == org_id)))
    models = list(db.scalars(select(LLMModel).where(LLMModel.org_id == org_id, LLMModel.enabled.is_(True))))
    for c in conns:
        mine = [m.model for m in models if m.connection_id == c.id]
        if not mine:
            continue
        host = _host(c.base_url) or PROVIDER_HOST.get(c.provider, c.provider)
        items.append(
            _item(
                f"llm:{c.id}",
                f"Language model: {c.provider}",
                "model",
                host,
                [
                    "your questions",
                    "retrieved passages of code and documents (secrets removed first)",
                    "investigation evidence",
                ],
                "Settings → AI & search → Models and routing",
                f"Models in use: {', '.join(mine)}",
            )
        )
    if not conns:  # the single-model setup or the server environment
        route = next(iter(llm_router.all_routes(db, org_id)), None)
        if route and route.cfg.provider != "heuristic":
            host = _host(route.cfg.base_url) or PROVIDER_HOST.get(route.cfg.provider, route.cfg.provider)
            items.append(
                _item(
                    "llm:single",
                    f"Language model: {route.cfg.provider}",
                    "model",
                    host,
                    ["your questions", "retrieved passages of code and documents (secrets removed first)"],
                    "Settings → AI & search → Language model",
                    f"Model: {route.cfg.model}",
                )
            )

    # embeddings and reranking
    if s.embedding_provider != "hash":
        items.append(
            _item(
                "embedding",
                "Embedding service",
                "embedding",
                _host(s.embedding_base_url),
                ["the text of every document and code chunk when indexing", "every question you search with"],
                "Server setting REPOLENS_EMBEDDING_*",
                f"Model: {s.embedding_model}",
            )
        )
    if s.rerank_provider == "api" and s.rerank_base_url:
        items.append(
            _item(
                "rerank",
                "Reranking service",
                "rerank",
                _host(s.rerank_base_url),
                ["the question and the top search results"],
                "Server setting REPOLENS_RERANK_*",
            )
        )

    # connectors
    gh = db.scalar(select(GithubConnection).where(GithubConnection.org_id == org_id))
    if gh is not None:
        items.append(
            _item(
                "github",
                "GitHub",
                "connector",
                _host(gh.api_base, "api.github.com"),
                ["repository, pull request and issue requests (reads); clones over the network"],
                "Settings → Integrations → GitHub",
                "Receives data from GitHub only; nothing of yours is posted.",
            )
        )
    bb = db.scalar(select(BitbucketConnection).where(BitbucketConnection.org_id == org_id))
    if bb is not None:
        items.append(
            _item(
                "bitbucket",
                "Bitbucket",
                "connector",
                _host(bb.base_url, "api.bitbucket.org"),
                ["repository, pull request and branch requests (reads)"],
                "Settings → Integrations → Bitbucket",
            )
        )
    jr = db.scalar(select(JiraConnection).where(JiraConnection.org_id == org_id))
    if jr is not None:
        items.append(
            _item(
                "jira",
                "Jira",
                "connector",
                _host(jr.base_url),
                [
                    "ticket requests (reads)",
                    "approved root-cause reports, only when you press publish and publishing is on",
                ],
                "Settings → Integrations → Jira",
                "Publishing is on." if jr.allow_publish else "Publishing is off: nothing is written to Jira.",
            )
        )
    cf = db.scalar(select(ConfluenceConnection).where(ConfluenceConnection.org_id == org_id))
    if cf is not None:
        items.append(
            _item(
                "confluence",
                "Confluence",
                "connector",
                _host(cf.base_url),
                ["page requests (reads)", "a comment on a page, only when you approve a proposed edit as a comment"],
                "Settings → Integrations → Confluence",
            )
        )
    kinds = sorted({w.kind for w in db.scalars(select(WikiSource).where(WikiSource.org_id == org_id))} - {"confluence"})
    for k in kinds:
        items.append(
            _item(
                f"source:{k}",
                f"Source: {k}",
                "connector",
                "",
                ["requests to read pages and messages"],
                "DeepMind Wiki → Sources",
                "Re-synced on the schedule set for each source.",
            )
        )

    # identity and telemetry
    sso = db.scalar(select(SsoConfig).where(SsoConfig.org_id == org_id, SsoConfig.enabled.is_(True)))
    if sso is not None:
        items.append(
            _item(
                "sso",
                "Single sign-on provider",
                "identity",
                _host(sso.issuer),
                ["a sign-in request carrying the person's e-mail, when someone signs in"],
                "Server setting: single sign-on",
            )
        )
    if s.otlp_endpoint:
        items.append(
            _item(
                "telemetry",
                "Trace collector",
                "telemetry",
                _host(s.otlp_endpoint),
                ["request traces: paths, timings and status codes, no bodies"],
                "Server setting REPOLENS_OTLP_ENDPOINT",
            )
        )

    external = [i for i in items if not i["stays_on_machine"]]
    return {
        "items": items,
        "external": len(external),
        "summary": "Nothing leaves this server."
        if not external
        else f"{len(external)} connection{'s' if len(external) != 1 else ''} send data off this server.",
        "local_only": [
            "Search, the knowledge graph, the wiki, ownership, change-risk scoring and the answer memory run on this server only.",
            "Passwords are hashed; API keys and tokens are encrypted and never returned.",
        ],
    }
