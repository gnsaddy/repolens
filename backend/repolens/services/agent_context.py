"""Context shared between agents.

The investigator distils what it found into a small digest. Agents that work on the result later (the runbook
writer, the doc updater, a follow-up investigation) read the digest instead of the full evidence again, so the
same facts are never paid for twice. Digests are stored in the cache table and expire.
"""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import CacheEntry, Investigation, _now

TASK = "handoff"
KEEP = timedelta(days=30)


def _key(workflow_id: str) -> str:
    return f"handoff:{workflow_id}"


def digest_of(inv: Investigation) -> dict:
    """The few facts every later agent needs, taken from a finished investigation."""
    top = inv.hypotheses[0] if inv.hypotheses else None
    refs: list[str] = []
    for e in (top.evidence if top else [])[:8]:
        kind = e.get("type")
        if kind == "code":
            refs.append(f"code {e.get('repo')}:{e.get('file')}:{e.get('start_line')}-{e.get('end_line')}")
        elif kind == "commit":
            first = (e.get("message") or "").splitlines()[:1]
            refs.append(f"commit {str(e.get('sha', ''))[:10]} {first[0][:80] if first else ''}".strip())
        elif kind == "pr":
            refs.append(f"PR #{e.get('number')} {e.get('title', '')[:80]}")
    return {
        "title": inv.title[:200],
        "problem": " ".join(inv.query.split())[:400],
        "root_cause": ((top.title if top else "") or inv.root_cause)[:300],
        "why": (top.explanation if top else "")[:500],
        "fix": inv.suggested_fix[:400],
        "confidence": round(inv.confidence or 0, 2),
        "confirmed": inv.root_cause_status == "confirmed",
        "evidence": refs,
    }


def publish(db: Session, org_id: str, workflow_id: str, digest: dict) -> None:
    db.execute(
        delete(CacheEntry).where(
            CacheEntry.org_id == org_id, CacheEntry.task == TASK, CacheEntry.created_at < _now() - KEEP
        )
    )
    row = db.get(CacheEntry, _key(workflow_id))
    if row is None:
        db.add(CacheEntry(key=_key(workflow_id), org_id=org_id, task=TASK, value=digest, tokens_baseline=0))
    else:
        row.value = digest
    db.commit()


def fetch(db: Session, org_id: str, workflow_id: str) -> dict | None:
    row = db.scalar(select(CacheEntry).where(CacheEntry.key == _key(workflow_id), CacheEntry.org_id == org_id))
    return dict(row.value) if row else None


def render(d: dict) -> str:
    """The digest as prompt text: short, labelled, and honest about whether a person has confirmed it."""
    lines = [
        f"Title: {d['title']}",
        f"Reported problem: {d['problem']}",
        f"Root cause ({'confirmed by a person' if d.get('confirmed') else 'not yet confirmed'}, "
        f"confidence {d.get('confidence', 0)}): {d['root_cause']}",
    ]
    if d.get("why"):
        lines.append(f"Why: {d['why']}")
    if d.get("fix"):
        lines.append(f"Suggested fix: {d['fix']}")
    if d.get("evidence"):
        lines.append("Evidence:\n" + "\n".join(f"- {e}" for e in d["evidence"]))
    return "\n".join(lines)
