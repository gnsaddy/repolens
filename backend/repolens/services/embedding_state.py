"""Which embedding model the stored vectors came from, so a change of model or size is noticed instead of
silently mixing incompatible vectors (search quietly gets worse, or fails where the column size is fixed)."""

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CacheEntry

KEY = "meta:embedder"


def current() -> dict:
    s = get_settings()
    return {
        "provider": s.embedding_provider,
        "model": "local-hash-v1" if s.embedding_provider == "hash" else s.embedding_model,
        "dim": s.embedding_dim,
    }


def recorded(db: Session, org_id: str) -> dict | None:
    row = db.get(CacheEntry, f"{KEY}:{org_id}"[:64])
    return dict(row.value) if row else None


def record(db: Session, org_id: str) -> None:
    key = f"{KEY}:{org_id}"[:64]
    row = db.get(CacheEntry, key)
    if row is None:
        db.add(CacheEntry(key=key, org_id=org_id, task="meta", value=current(), tokens_baseline=0))
    else:
        row.value = current()
    db.commit()


def status(db: Session, org_id: str) -> dict:
    """`stale` when the vectors were made by something other than the configured embedder."""
    now, was = current(), recorded(db, org_id)
    stale = was is not None and was != now
    return {
        "current": now,
        "indexed_with": was,
        "stale": stale,
        "message": (
            f"Stored vectors came from {was['model']} ({was['dim']} dimensions) but the server now uses {now['model']} "
            f"({now['dim']}). Re-index your repositories and documents so search matches the new model."
            if stale and was
            else ""
        ),
    }
