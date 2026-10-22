import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import LLMSetting
from ..schemas import LLMSettingIn
from ..security import encrypt_secret, redact_secrets
from ..services.jira import JiraError, validate_base_url
from ..services.llm import CHAT_PROVIDERS, LLMClient, LLMUnavailable, github_token, resolve_config
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
def retrieval_status(p: Principal = Depends(current_principal)):
    """Read-only view of how search is configured on this server (set with REPOLENS_* environment variables)."""
    s = get_settings()
    return {
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
        return {"ok": False, "error": redact_secrets(f"{type(e).__name__}: {e}")[:300]}
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
