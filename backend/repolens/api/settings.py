import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import LLMConnection, LLMModel, LLMSetting
from ..schemas import ConnectionIn, LLMCheckIn, LLMSettingIn, ModelPatch, RoutingIn, SkillIn
from ..security import decrypt_secret, encrypt_secret, redact_secrets
from ..services import answer_memory, embedding_state, llm_router
from ..services.access import scope_for
from ..services.jira import JiraError, validate_base_url
from ..services.llm import (
    CHAT_PROVIDERS,
    GEMINI_URL,
    LLMClient,
    LLMConfig,
    LLMUnavailable,
    github_token,
    list_models,
    ping,
    resolve_config,
)
from ..services.policy import authorize
from ..services.usage import BudgetExceeded

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Overridable in tests to inject a mock transport.
_catalog_transport: httpx.BaseTransport | None = None

PRESETS = {
    "anthropic": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"],
    "openai": [],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],  # suggestions; any model id works
    "github-models": [
        "openai/gpt-4.1",
        "openai/gpt-4.1-mini",
    ],  # full list: GET /api/settings/llm/github-models/catalog
}


def _own_key(db: Session, org_id: str) -> bool:
    row = db.scalar(select(LLMSetting).where(LLMSetting.org_id == org_id))
    return bool(row and row.api_key_encrypted) or bool(get_settings().llm_api_key)


def _view(db: Session, org_id: str) -> dict:
    cfg = resolve_config(db, org_id)
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url if cfg.provider == "openai" or cfg.source == "org" else "",
        "has_key": bool(cfg.api_key),
        "source": cfg.source,
        "input_price": cfg.input_price,
        "output_price": cfg.output_price,
        "offline": cfg.provider not in CHAT_PROVIDERS,
        "uses_github_token": cfg.provider == "github-models" and not _own_key(db, org_id),
        "presets": PRESETS,
        "embedding_provider": get_settings().embedding_provider,
    }


@router.get("/retrieval")
def retrieval_status(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Read-only view of how search is configured on this server (set with REPOLENS_* environment variables)."""
    s = get_settings()
    return {
        "embedding_state": embedding_state.status(db, p.org_id),
        "embedding": {
            "provider": s.embedding_provider,
            "model": "local-hash-v1" if s.embedding_provider == "hash" else s.embedding_model,
            "dimensions": s.embedding_dim,
            "offline": s.embedding_provider == "hash",
        },
        "rerank": {
            "provider": s.rerank_provider,
            "model": s.rerank_model
            if s.rerank_provider == "api"
            else "built-in lexical cross-scoring"
            if s.rerank_provider == "lexical"
            else "",
            "configured": s.rerank_provider != "api" or bool(s.rerank_base_url),
            "depth": s.rerank_depth,
        },
    }


@router.get("/llm")
def get_llm(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _view(db, p.org_id)


@router.put("/llm")
def put_llm(body: LLMSettingIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "llm.configure", body.provider, model=body.model)
    if body.base_url:
        try:
            validate_base_url(body.base_url)
        except JiraError as e:
            raise HTTPException(400, str(e).replace("Jira URL", "Base URL")) from e
    row = db.scalar(select(LLMSetting).where(LLMSetting.org_id == p.org_id))
    if row is None:
        row = LLMSetting(org_id=p.org_id)
        db.add(row)
    existing_key = bool(row.api_key_encrypted) or bool(get_settings().llm_api_key)
    github_ok = body.provider == "github-models" and bool(github_token(db, p.org_id))
    if body.provider != "heuristic" and not body.api_key and not existing_key and not github_ok:
        raise HTTPException(
            400,
            "Connect GitHub first (or paste a token) to use GitHub Models"
            if body.provider == "github-models"
            else "An API key is required for this provider",
        )
    row.provider, row.model, row.base_url = body.provider, body.model, body.base_url
    if body.api_key:  # blank = keep the stored key
        row.api_key_encrypted = encrypt_secret(body.api_key)
    row.input_price, row.output_price, row.updated_by = body.input_price, body.output_price, p.user_id
    db.commit()
    return _view(db, p.org_id)


@router.delete("/llm")
def reset_llm(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Revert to the server's environment defaults and forget the stored key."""
    authorize(db, p, "llm.configure", "reset")
    row = db.scalar(select(LLMSetting).where(LLMSetting.org_id == p.org_id))
    if row:
        db.delete(row)
        db.commit()
    return _view(db, p.org_id)


def _scrub(text: str, key: str) -> str:
    """Remove secrets by pattern, and the exact key whatever it looks like, from text a provider echoed back."""
    out = redact_secrets(text)
    return out.replace(key, "[REDACTED]") if len(key) >= 8 else out


def _stored_key(db: Session, org_id: str, provider: str, base_url: str = "") -> str:
    """The key already saved for this provider: in the multi-model registry, else the single legacy setting."""
    base = "" if provider != "openai" else base_url
    conn = db.scalar(
        select(LLMConnection).where(
            LLMConnection.org_id == org_id, LLMConnection.provider == provider, LLMConnection.base_url == base
        )
    )
    if conn is not None and conn.api_key_encrypted:
        return decrypt_secret(conn.api_key_encrypted)
    stored = resolve_config(db, org_id)
    return stored.api_key if stored.provider == provider else ""


@router.post("/llm/check")
def check_llm(body: LLMCheckIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Try a provider and key before saving anything. Without a model it lists the models the key can use;
    with a model it sends one tiny test message."""
    authorize(db, p, "llm.test", f"check-{body.provider}")
    key = body.api_key.strip() or _stored_key(db, p.org_id, body.provider, body.base_url.strip())
    if not key:
        return {"ok": False, "error": "Paste an API key first"}
    base = {"gemini": GEMINI_URL, "anthropic": "", "openai": body.base_url.strip() or "https://api.openai.com/v1"}[
        body.provider
    ]
    if body.provider == "openai":
        try:
            validate_base_url(base)
        except JiraError as e:
            return {"ok": False, "error": str(e).replace("Jira URL", "Base URL")}
    cfg = LLMConfig(body.provider, body.model, base, key, "org")
    try:
        if body.model:
            return {"ok": True, **ping(cfg)}
        return {"ok": True, "models": list_models(cfg)}
    except Exception as e:  # provider errors must never leak the key
        text = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
        return {"ok": False, "error": _scrub(text, key)[:300]}


@router.get("/llm/models")
def llm_models(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Models the saved key can use (Gemini, Anthropic and OpenAI-compatible services)."""
    authorize(db, p, "llm.test", "models")
    cfg = resolve_config(db, p.org_id)
    if cfg.provider not in ("gemini", "anthropic", "openai"):
        raise HTTPException(400, "Listing models is available for Gemini, Anthropic and OpenAI-compatible providers")
    try:
        models = list_models(cfg)
    except RuntimeError as e:
        raise HTTPException(502, _scrub(str(e), cfg.api_key)) from None
    return {
        "provider": cfg.provider,
        "current": cfg.model,
        "current_found": any(m["id"] == cfg.model for m in models),
        "models": models,
    }


@router.post("/llm/test")
def test_llm(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "llm.test", "ping")
    client = LLMClient(db, p.org_id, workflow="settings", workflow_id="")
    if client.offline:
        return {"ok": False, "error": "No hosted model is configured (offline mode)."}
    started = time.time()
    try:
        res = client.complete("settings-test", "Reply with the single word OK.", "ping", max_tokens=16)
    except (BudgetExceeded, LLMUnavailable) as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # provider errors must not leak keys
        text = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
        return {"ok": False, "error": _scrub(text, client.cfg.api_key)[:300]}
    return {
        "ok": True,
        "model": client.model,
        "latency_ms": int((time.time() - started) * 1000),
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
    }


@router.get("/llm/github-models/catalog")
def github_models_catalog(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Models available to the connected GitHub account (GitHub Models catalog)."""
    authorize(db, p, "llm.test", "github-models-catalog")
    cfg = resolve_config(db, p.org_id)
    token = cfg.api_key if cfg.provider == "github-models" else github_token(db, p.org_id)
    if not token:
        raise HTTPException(400, "Connect GitHub first")
    try:
        with httpx.Client(timeout=20, transport=_catalog_transport) as hc:
            r = hc.get(
                "https://models.github.ai/catalog/models",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, redact_secrets(f"GitHub Models catalog unavailable: {type(e).__name__}")) from e
    except ValueError:
        raise HTTPException(
            502,
            f"GitHub Models sent a non-JSON reply ({r.text[:20].strip()!r}); it may be unavailable from this network",
        ) from None
    items = data if isinstance(data, list) else data.get("models", [])
    return [
        {"id": m.get("id"), "name": m.get("name", ""), "publisher": m.get("publisher", "")}
        for m in items
        if m.get("id")
    ][:200]


# ------------------------------------------------------------------------------------------------ multi-model registry
def _registry_view(db: Session, org_id: str) -> dict:
    conns = db.scalars(
        select(LLMConnection).where(LLMConnection.org_id == org_id).order_by(LLMConnection.created_at)
    ).all()
    models = db.scalars(select(LLMModel).where(LLMModel.org_id == org_id).order_by(LLMModel.created_at)).all()
    out = []
    for c in conns:
        out.append(
            {
                "id": c.id,
                "provider": c.provider,
                "base_url": c.base_url or None,
                "has_key": bool(c.api_key_encrypted),
                "models": [
                    {
                        "id": m.id,
                        "model": m.model,
                        "tier": m.tier,
                        "enabled": m.enabled,
                        "input_price": m.input_price,
                        "output_price": m.output_price,
                    }
                    for m in models
                    if m.connection_id == c.id
                ],
            }
        )
    routes = llm_router.all_routes(db, org_id)
    savings = llm_router.savings_mode(db, org_id)
    legacy = not conns and bool(routes)  # one model from the single-model setup or the server environment
    if legacy:  # show it like a one-model connection so the page has one shape
        r = routes[0].cfg
        out.append(
            {
                "id": "legacy",
                "provider": r.provider,
                "base_url": None,
                "has_key": True,
                "legacy": True,
                "models": [
                    {
                        "id": "legacy",
                        "model": r.model,
                        "tier": "standard",
                        "enabled": True,
                        "input_price": r.input_price,
                        "output_price": r.output_price,
                    }
                ],
            }
        )
    return {
        "connections": out,
        "savings_mode": savings,
        "plan": llm_router.plan(routes, savings),
        "multi": len(routes) > 1,
        "active": len(routes),
    }


def _adopt_legacy(db: Session, org_id: str) -> None:
    """The first time models are managed here, keep the single model that was already working."""
    if db.scalar(select(LLMConnection.id).where(LLMConnection.org_id == org_id).limit(1)):
        return
    row = db.scalar(select(LLMSetting).where(LLMSetting.org_id == org_id))
    if row is None or row.provider not in ("anthropic", "openai", "gemini") or not row.api_key_encrypted:
        return
    base = row.base_url if row.provider == "openai" else ""
    conn = LLMConnection(org_id=org_id, provider=row.provider, base_url=base, api_key_encrypted=row.api_key_encrypted)
    db.add(conn)
    db.flush()
    db.add(
        LLMModel(
            org_id=org_id,
            connection_id=conn.id,
            model=row.model,
            tier="standard",
            input_price=row.input_price,
            output_price=row.output_price,
        )
    )
    db.flush()


@router.get("/models")
def get_registry(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Connected providers, their models and tiers, and what each kind of task would use right now."""
    return _registry_view(db, p.org_id)


def _verify_new_models(db: Session, org_id: str, body: ConnectionIn, base: str, conn: LLMConnection | None) -> None:
    """A model is only saved after it answered a live test with this key, whatever the client did before."""
    key = body.api_key.strip() or (decrypt_secret(conn.api_key_encrypted) if conn and conn.api_key_encrypted else "")
    known = {m.model for m in db.scalars(select(LLMModel).where(LLMModel.connection_id == conn.id))} if conn else set()
    failed = []
    for spec in body.models:
        if spec.model in known and not body.api_key.strip():
            continue  # already working; only its tier or price is changing
        if not key:
            continue  # the "key required" error below is clearer
        try:
            ping(LLMConfig(body.provider, spec.model, GEMINI_URL if body.provider == "gemini" else base, key, "org"))
        except Exception as e:  # noqa: BLE001 - any provider error means the model is not usable
            text = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            failed.append(f"{spec.model}: {_scrub(text, key)[:160]}")
    if failed:
        raise HTTPException(
            400, "These models did not answer a test message, so nothing was saved. " + "; ".join(failed)
        )


@router.post("/models/connections", status_code=201)
def save_connection(body: ConnectionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Add or update a provider and the models to route to. Models not listed are left as they are."""
    authorize(db, p, "llm.configure", body.provider, op="registry")
    base = ""
    if body.provider == "openai":
        base = body.base_url.strip() or "https://api.openai.com/v1"
        try:
            validate_base_url(base)
        except JiraError as e:
            raise HTTPException(400, str(e).replace("Jira URL", "Base URL")) from e
    conn = db.scalar(
        select(LLMConnection).where(
            LLMConnection.org_id == p.org_id, LLMConnection.provider == body.provider, LLMConnection.base_url == base
        )
    )
    _verify_new_models(db, p.org_id, body, base, conn)
    _adopt_legacy(db, p.org_id)
    conn = db.scalar(
        select(LLMConnection).where(
            LLMConnection.org_id == p.org_id, LLMConnection.provider == body.provider, LLMConnection.base_url == base
        )
    )
    if conn is None:
        if not body.api_key.strip():
            raise HTTPException(400, "An API key is required for a new provider")
        conn = LLMConnection(org_id=p.org_id, provider=body.provider, base_url=base)
        db.add(conn)
    if body.api_key.strip():
        conn.api_key_encrypted = encrypt_secret(body.api_key.strip())
    db.flush()
    existing = {m.model: m for m in db.scalars(select(LLMModel).where(LLMModel.connection_id == conn.id))}
    for spec in body.models:
        m = existing.get(spec.model)
        if m is None:
            m = LLMModel(org_id=p.org_id, connection_id=conn.id, model=spec.model)
            db.add(m)
        m.tier, m.enabled, m.input_price, m.output_price = spec.tier, True, spec.input_price, spec.output_price
    db.commit()
    return _registry_view(db, p.org_id)


def _model_row(db: Session, p: Principal, model_id: str) -> LLMModel:
    m = db.get(LLMModel, model_id)
    if m is None or m.org_id != p.org_id:
        raise HTTPException(404, "Model not found")
    return m


@router.put("/models/{model_id}")
def patch_model(
    model_id: str, body: ModelPatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    authorize(db, p, "llm.configure", model_id, op="model")
    m = _model_row(db, p, model_id)
    for field in ("tier", "enabled"):
        if (v := getattr(body, field)) is not None:
            setattr(m, field, v)
    if "input_price" in body.model_fields_set:
        m.input_price = body.input_price
    if "output_price" in body.model_fields_set:
        m.output_price = body.output_price
    db.commit()
    return _registry_view(db, p.org_id)


@router.delete("/models/{model_id}")
def delete_model(model_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "llm.configure", model_id, op="model-delete")
    m = _model_row(db, p, model_id)
    conn_id = m.connection_id
    db.delete(m)
    db.flush()
    if not db.scalar(select(LLMModel.id).where(LLMModel.connection_id == conn_id).limit(1)):
        conn = db.get(LLMConnection, conn_id)
        if conn is not None:
            db.delete(conn)  # a provider with no models has nothing to keep a key for
    db.commit()
    return _registry_view(db, p.org_id)


@router.delete("/models/connections/{conn_id}")
def delete_connection(conn_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "llm.configure", conn_id, op="connection-delete")
    c = db.get(LLMConnection, conn_id)
    if c is None or c.org_id != p.org_id:
        raise HTTPException(404, "Provider not found")
    for m in db.scalars(select(LLMModel).where(LLMModel.connection_id == c.id)):
        db.delete(m)
    db.delete(c)
    db.commit()
    return _registry_view(db, p.org_id)


@router.put("/routing")
def set_routing(body: RoutingIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Savings mode: send plain tasks to cheaper models. Off means every task uses its normal tier."""
    authorize(db, p, "llm.configure", "routing", savings_mode=body.savings_mode)
    row = db.scalar(select(LLMSetting).where(LLMSetting.org_id == p.org_id))
    if row is None:
        row = LLMSetting(org_id=p.org_id)
        db.add(row)
    row.savings_mode, row.updated_by = body.savings_mode, p.user_id
    db.commit()
    return _registry_view(db, p.org_id)


@router.get("/memory")
def memory_stats(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What the local answer memory has saved so far."""
    return answer_memory.stats(db, p.org_id)


@router.delete("/memory")
def memory_clear(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "llm.configure", "answer-memory")
    return {"cleared": answer_memory.clear(db, p.org_id)}


@router.get("/memory/entries")
def memory_entries(
    q: str = "",
    deep: bool = True,
    limit: int = 40,
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Browse remembered answers, or search them (deep search also reads the answers)."""
    return answer_memory.browse(db, p.org_id, scope_for(db, p), p.role == "admin", q, deep, max(1, min(limit, 100)))


@router.delete("/memory/entries/{entry_id}", status_code=204)
def memory_forget(entry_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "llm.configure", "answer-memory", op="forget")
    if not answer_memory.delete_entry(db, p.org_id, entry_id):
        raise HTTPException(404, "Not found")


@router.post("/memory/skill")
def memory_skill(body: SkillIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Package selected answers as a Claude skill (SKILL.md) to download or copy."""
    authorize(db, p, "wiki.propose", "memory-skill", count=len(body.entry_ids))
    try:
        return answer_memory.build_skill(db, p.org_id, scope_for(db, p), p.role == "admin", body.entry_ids, body.name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
