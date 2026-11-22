"""A short record of each webhook GitHub sends, so an admin can see the connection works and what became of each.

Only the event, the action, the repository name and a one line outcome are kept (never the payload), and only the newest
few hundred rows."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import WebhookDelivery

KEEP = 300  # rows kept in all
KEEP_REFUSED = 50  # of those, refused deliveries (anyone who knows the address can send one)
REPEAT_SECONDS = 60  # an identical refused delivery inside this time adds no row
SHOWN = 20  # rows shown on the Agent page
STALE_DAYS = 30


def _clip(text: object, n: int) -> str:
    return str(text or "")[:n]


def note_of(result: dict) -> str:
    """One short line about what a handler did, for example `review=1 pr_synced=1`."""
    parts = []
    for k, v in result.items():
        if isinstance(v, list):
            v = len(v)
        parts.append(f"{k}={v}")
    return _clip(" ".join(parts), 300)


def record(
    db: Session,
    *,
    org_ids: list[str] | None,
    delivery_id: str,
    event: str,
    action: str = "",
    repo: str = "",
    outcome: str = "ok",
    note: str = "",
) -> None:
    """Write one row for each workspace the delivery belongs to (one with no workspace when no repository matched)."""
    if outcome == "rejected" and db.scalar(
        select(WebhookDelivery.id).where(
            WebhookDelivery.outcome == "rejected",
            WebhookDelivery.event == _clip(event, 40),
            WebhookDelivery.note == _clip(note, 300),
            WebhookDelivery.created_at > datetime.now(UTC) - timedelta(seconds=REPEAT_SECONDS),
        )
    ):
        return  # the same refusal a moment ago is already shown
    for org in dict.fromkeys(org_ids or [""]):
        db.add(
            WebhookDelivery(
                org_id=org,
                delivery_id=_clip(delivery_id, 64),
                event=_clip(event, 40),
                action=_clip(action, 40),
                repo=_clip(repo, 200) if org else "",  # a name is shown only to the workspace that owns the repository
                outcome=outcome,
                note=_clip(note, 300),
            )
        )
    db.commit()
    prune(db)


def prune(db: Session) -> None:
    refused = db.scalars(
        select(WebhookDelivery.id)
        .where(WebhookDelivery.outcome == "rejected")
        .order_by(WebhookDelivery.created_at.desc())
        .limit(KEEP_REFUSED)
    ).all()
    if len(refused) >= KEEP_REFUSED:  # a flood of refusals never pushes out the real deliveries
        db.execute(
            delete(WebhookDelivery).where(WebhookDelivery.outcome == "rejected", WebhookDelivery.id.notin_(refused))
        )
    keep = db.scalars(select(WebhookDelivery.id).order_by(WebhookDelivery.created_at.desc()).limit(KEEP)).all()
    old = datetime.now(UTC) - timedelta(days=STALE_DAYS)
    if len(keep) >= KEEP:
        db.execute(delete(WebhookDelivery).where(WebhookDelivery.id.notin_(keep)))
    db.execute(delete(WebhookDelivery).where(WebhookDelivery.created_at < old))
    db.commit()


def recent(db: Session, org_id: str, limit: int = SHOWN) -> list[WebhookDelivery]:
    """This workspace's deliveries and the ones no repository claimed (rejected, or for a repository not added)."""
    return list(
        db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.org_id.in_((org_id, "")))
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
        )
    )
