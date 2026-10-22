"""Provider-neutral LLM adapter. Config comes from the organisation's settings (or env defaults); every call is
metered through services.usage."""

import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GithubConnection, LLMSetting
from ..security import decrypt_secret, redact_secrets
from . import usage


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key: str
    source: str  # "org" | "env"
    input_price: float | None = None
    output_price: float | None = None

    @property
    def pricing(self) -> dict[str, float] | None:
        if self.input_price is None or self.output_price is None:
            return None
        return {"input": self.input_price, "output": self.output_price, "cached_input": self.input_price}


class LLMUnavailable(Exception):
    """Raised when no hosted model is configured (offline/heuristic mode)."""


GITHUB_MODELS_URL = "https://models.github.ai/inference"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"  # Google's OpenAI-compatible endpoint
CHAT_PROVIDERS = ("anthropic", "openai", "github-models", "gemini")


def github_token(db: Session, org_id: str) -> str:
    c = db.scalar(select(GithubConnection).where(GithubConnection.org_id == org_id))
    return decrypt_secret(c.token_encrypted) if c and c.token_encrypted else ""


def resolve_config(db: Session, org_id: str) -> LLMConfig:
    row = db.scalar(select(LLMSetting).where(LLMSetting.org_id == org_id))
    s = get_settings()
    if row is None:
        cfg = LLMConfig(s.llm_provider, s.llm_model, s.llm_base_url, s.llm_api_key, "env")
    else:
        key = decrypt_secret(row.api_key_encrypted) if row.api_key_encrypted else s.llm_api_key
        cfg = LLMConfig(
            row.provider,
            row.model or s.llm_model,
            row.base_url or s.llm_base_url,
            key,
            "org",
            row.input_price,
            row.output_price,
        )
    if cfg.provider == "gemini":
        cfg.base_url = GEMINI_URL  # fixed: the key is only ever sent to Google
    if cfg.provider == "github-models":
        if "api.openai.com" in cfg.base_url:  # the default base URL is OpenAI's; GitHub Models has its own
            cfg.base_url = GITHUB_MODELS_URL
        if not cfg.api_key:  # fall back to the connected GitHub account's token
            cfg.api_key = github_token(db, org_id)
    return cfg


def _anthropic_call(system: str, prompt: str, max_tokens: int, cfg: LLMConfig) -> LLMResult:
    import anthropic

    kwargs: dict = {"api_key": cfg.api_key or None}
    if cfg.base_url and "api.openai.com" not in cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    client = anthropic.Anthropic(**kwargs)
    msg = client.messages.create(
        model=cfg.model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": prompt}]
    )
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    u = msg.usage
    return LLMResult(text, u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", 0) or 0)


# Overridable in tests to inject a mock transport.
_openai_transport: httpx.BaseTransport | None = None


def _openai_call(system: str, prompt: str, max_tokens: int, cfg: LLMConfig) -> LLMResult:
    with httpx.Client(timeout=120, transport=_openai_transport) as hc:
        r = hc.post(
            f"{cfg.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            json={
                "model": cfg.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            },
        )
    if r.status_code in (401, 403):
        raise RuntimeError(
            f"The model service rejected the credentials ({r.status_code}). Check the key or token and its permissions."
        )
    r.raise_for_status()
    try:
        d = r.json()
    except ValueError:
        host = httpx.URL(cfg.base_url).host
        raise RuntimeError(
            f"{host} answered {r.status_code} with something that is not a model reply "
            f"({r.headers.get('content-type', 'unknown type')}: {r.text[:40].strip()!r}). The service may be "
            "unavailable from this network, not enabled for this account, or the base URL is wrong. "
            "Try another provider or model."
            + (
                " GitHub retired GitHub Models on 30 July 2026, so this provider no longer works."
                if host.endswith("github.ai")
                else ""
            )
        ) from None
    if "choices" not in d:
        raise RuntimeError(f"The model service returned an unexpected reply: {str(d)[:120]!r}")
    u = d.get("usage", {})
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return LLMResult(
        d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0), cached
    )


class LLMClient:
    def __init__(
        self, db: Session, org_id: str, workflow: str = "rca", workflow_id: str = "", jira_issue_key: str | None = None
    ):
        self.db, self.org_id, self.workflow, self.workflow_id = db, org_id, workflow, workflow_id
        self.jira_issue_key = jira_issue_key
        self.cfg = resolve_config(db, org_id)
        self.provider, self.model = self.cfg.provider, self.cfg.model

    @property
    def offline(self) -> bool:
        return self.provider not in CHAT_PROVIDERS

    def complete(self, agent: str, system: str, prompt: str, max_tokens: int = 1500) -> LLMResult:
        if self.offline:
            raise LLMUnavailable("no hosted LLM configured")
        usage.check_budget(self.db, self.org_id)  # may raise BudgetExceeded
        prompt = redact_secrets(prompt)
        started = time.time()
        try:
            call = (
                _anthropic_call if self.provider == "anthropic" else _openai_call
            )  # github-models and gemini speak the OpenAI chat-completions protocol
            res = call(system, prompt, max_tokens, self.cfg)
        except Exception as e:
            usage.record_event(
                self.db,
                self.org_id,
                agent=agent,
                provider=self.provider,
                model=self.model,
                workflow=self.workflow,
                workflow_id=self.workflow_id,
                latency_ms=int((time.time() - started) * 1000),
                success=False,
                error=redact_secrets(str(e))[:500],
                jira_issue_key=self.jira_issue_key,
            )
            raise
        usage.record_event(
            self.db,
            self.org_id,
            agent=agent,
            provider=self.provider,
            model=self.model,
            workflow=self.workflow,
            workflow_id=self.workflow_id,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cached_input_tokens=res.cached_input_tokens,
            latency_ms=int((time.time() - started) * 1000),
            jira_issue_key=self.jira_issue_key,
            pricing_override=self.cfg.pricing,
        )
        return res
