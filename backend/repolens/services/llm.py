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
    route: dict | None = None  # which model answered and why (set by LLMClient)
    truncated: bool = False  # the model stopped because it ran out of output budget, not because it finished


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
    return LLMResult(
        text,
        u.input_tokens,
        u.output_tokens,
        getattr(u, "cache_read_input_tokens", 0) or 0,
        truncated=getattr(msg, "stop_reason", "") == "max_tokens",
    )


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
    if r.status_code == 404:
        raise RuntimeError(
            f"{httpx.URL(cfg.base_url).host} says the model '{cfg.model}' was not found (404). "
            "Use Test connection and pick a model from the list it shows."
        )
    if r.status_code >= 400:
        raise RuntimeError(f"{httpx.URL(cfg.base_url).host} returned {r.status_code}: {_error_text(r)}")
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
    choice = d["choices"][0]
    return LLMResult(
        choice["message"]["content"] or "",
        u.get("prompt_tokens", 0),
        u.get("completion_tokens", 0),
        cached,
        truncated=choice.get("finish_reason") == "length",
    )


def ping(cfg: LLMConfig) -> dict:
    """One tiny chat call with an explicit config (used to try credentials before they are saved)."""
    started = time.time()
    call = _anthropic_call if cfg.provider == "anthropic" else _openai_call
    res = call("Reply with the single word OK.", "ping", 16, cfg)
    return {"model": cfg.model, "latency_ms": int((time.time() - started) * 1000), "input_tokens": res.input_tokens}


def _error_text(r: httpx.Response) -> str:
    """The provider's own error message (never the key), shortened."""
    try:
        d = r.json()
        d = d[0] if isinstance(d, list) and d else d
        msg = (d.get("error") or {}).get("message") if isinstance(d, dict) else None
        return str(msg or d)[:200]
    except ValueError:
        return r.text[:200].strip() or "no details"


# Overridable in tests.
_models_transport: httpx.BaseTransport | None = None
GEMINI_NATIVE = "https://generativelanguage.googleapis.com/v1beta"


def list_models(cfg: LLMConfig) -> list[dict]:
    """Models the configured key can use: [{id, name}]. Raises RuntimeError with a safe message."""
    if not cfg.api_key:
        raise RuntimeError("Save an API key first")
    with httpx.Client(timeout=30, transport=_models_transport, follow_redirects=False) as hc:
        try:
            if (
                cfg.provider == "gemini"
            ):  # the native listing says which models can chat; the key goes in a header, not the URL
                out: list[dict] = []
                token = ""
                for _ in range(5):
                    r = hc.get(
                        f"{GEMINI_NATIVE}/models",
                        headers={"x-goog-api-key": cfg.api_key},
                        params={"pageSize": 200, **({"pageToken": token} if token else {})},
                    )
                    _check(r, "Google")
                    d = r.json()
                    out += [
                        {"id": m["name"].removeprefix("models/"), "name": m.get("displayName", "")}
                        for m in d.get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", []) and m.get("name")
                    ]
                    token = d.get("nextPageToken", "")
                    if not token:
                        break
                return sorted(out, key=lambda m: m["id"])
            if cfg.provider == "anthropic":
                r = hc.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"},
                    params={"limit": 100},
                )
                _check(r, "Anthropic")
                return [{"id": m["id"], "name": m.get("display_name", "")} for m in r.json().get("data", [])]
            if cfg.provider == "openai":
                r = hc.get(f"{cfg.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {cfg.api_key}"})
                _check(r, httpx.URL(cfg.base_url).host)
                return sorted(
                    ({"id": m["id"], "name": ""} for m in r.json().get("data", []) if m.get("id")),
                    key=lambda m: m["id"],
                )
        except httpx.HTTPError as e:
            raise RuntimeError(f"The model service is unreachable ({type(e).__name__})") from None
        except (ValueError, KeyError):
            raise RuntimeError("The model service returned a list in an unexpected format") from None
    raise RuntimeError("Listing models is not available for this provider")


def _check(r: httpx.Response, who: str) -> None:
    if r.status_code in (401, 403):
        raise RuntimeError(f"{who} rejected the key ({r.status_code}). Check it and its permissions.")
    if r.status_code >= 400:
        raise RuntimeError(f"{who} returned {r.status_code} when listing models: {_error_text(r)}")


class LLMClient:
    """Calls the organisation's models. With several configured, each call goes to the cheapest model that is good
    enough for the task (see llm_router), falling back to the next one if a model fails."""

    def __init__(
        self, db: Session, org_id: str, workflow: str = "rca", workflow_id: str = "", jira_issue_key: str | None = None
    ):
        from . import llm_router

        self.db, self.org_id, self.workflow, self.workflow_id = db, org_id, workflow, workflow_id
        self.jira_issue_key = jira_issue_key
        self._routes = llm_router.all_routes(db, org_id)
        self.savings = llm_router.savings_mode(db, org_id)
        default = next((r for r in self._routes if r.tier == "standard"), self._routes[0] if self._routes else None)
        self.cfg = default.cfg if default else resolve_config(db, org_id)
        self.provider, self.model = self.cfg.provider, self.cfg.model

    @property
    def offline(self) -> bool:
        return not self._routes

    @property
    def cache_id(self) -> str:
        """Stable id for answer caches: the model when there is one, otherwise 'auto' (the router picks per task)."""
        return (
            f"{self.provider}/{self.model}"
            if len(self._routes) <= 1
            else "auto:" + ",".join(sorted(r.cfg.model for r in self._routes))
        )

    def decide(self, task):
        from . import llm_router

        low = False
        try:
            low = usage.budget_low(self.db, self.org_id)
        except Exception:  # an advisory signal must never block a call
            low = False
        return llm_router.decide(self._routes, task, self.savings, low)

    def _call(self, route, agent: str, system: str, prompt: str, max_tokens: int) -> LLMResult:
        cfg = route.cfg
        started = time.time()
        try:
            call = (
                _anthropic_call if cfg.provider == "anthropic" else _openai_call
            )  # gemini and github-models speak OpenAI chat
            res = call(system, prompt, max_tokens, cfg)
        except Exception as e:
            usage.record_event(
                self.db,
                self.org_id,
                agent=agent,
                provider=cfg.provider,
                model=cfg.model,
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
            provider=cfg.provider,
            model=cfg.model,
            workflow=self.workflow,
            workflow_id=self.workflow_id,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cached_input_tokens=res.cached_input_tokens,
            latency_ms=int((time.time() - started) * 1000),
            jira_issue_key=self.jira_issue_key,
            pricing_override=cfg.pricing,
        )
        return res

    def complete(
        self, agent: str, system: str, prompt: str, max_tokens: int = 1500, task=None, validate=None
    ) -> LLMResult:
        """`task` describes how hard this is (defaults to the agent name); `validate(text)` lets the caller reject an
        unusable answer, in which case one stronger model is tried before giving up."""
        from . import llm_router

        if self.offline:
            raise LLMUnavailable("no hosted LLM configured")
        usage.check_budget(self.db, self.org_id)  # may raise BudgetExceeded
        prompt = redact_secrets(prompt)
        task = task or llm_router.Task(agent, prompt_chars=len(prompt))
        decision = self.decide(task)
        last: Exception | None = None
        for i, route in enumerate(decision.chain):
            try:
                res = self._call(route, agent, system, prompt, max_tokens)
            except Exception as e:  # rate limit, outage, unknown model: try the next model
                last = e
                continue
            info = {
                "model": route.cfg.model,
                "provider": route.cfg.provider,
                "tier": route.tier,
                "why": decision.why,
                "fallbacks": i,
                "escalated": False,
            }
            if validate is not None and not validate(res.text):
                up = llm_router.stronger(decision.chain, route)
                if up is not None:
                    try:
                        res2 = self._call(up, agent, system, prompt, max_tokens)
                        res2.route = {
                            **info,
                            "model": up.cfg.model,
                            "provider": up.cfg.provider,
                            "tier": up.tier,
                            "escalated": True,
                            "why": [*decision.why, f"{route.cfg.model} gave an unusable answer"],
                        }
                        return res2
                    except Exception as e:
                        last = e
            res.route = info
            return res
        assert last is not None
        raise last


def describe_failure(e: Exception, llm: LLMClient) -> str:
    """Why the model did not answer, in words a person can act on (provider errors are already scrubbed of keys)."""
    msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    low = msg.lower()
    if type(e).__name__ == "BudgetExceeded":
        why = "This workspace's token budget is used up. An admin can raise it on the Usage page."
    elif "rejected the credentials" in low or "401" in low or "403" in low:
        why = "The provider rejected the API key. Check it in Settings → AI & search."
    elif "not found (404)" in low or "was not found" in low:
        why = f"The model name was not recognised by the provider. {msg}"
    elif "429" in low or "rate limit" in low or "quota" in low or "resource_exhausted" in low:
        why = "The provider is limiting requests (rate limit or quota). Wait and try again, or check your quota."
    elif "timed out" in low or "timeout" in low:
        why = "The model took too long to respond. Try again."
    elif any(x in low for x in ("connect", "network", "name or service", "unreachable")):
        why = f"The model service could not be reached from the server. {msg}"
    else:
        why = msg[:240]
    return f"{why} (model: {llm.provider} / {llm.model or 'default'})"
