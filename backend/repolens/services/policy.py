"""Policy gateway: deny by default, check role + action policy, audit every decision."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..deps import Principal, audit

ACTION_MIN_ROLE = {
    "repo.create": "member",
    "repo.index": "member",
    "repo.delete": "admin",
    "investigation.create": "member",
    "investigation.delete": "member",
    "investigation.review": "member",
    "jira.configure": "admin",
    "jira.read": "member",
    "jira.publish": "member",
    "user.create": "admin",
    "collection.create": "member",
    "collection.delete": "admin",
    "document.write": "member",
    "document.delete": "member",
    "llm.configure": "admin",
    "github.configure": "admin",
    "github.sync": "member",
    "llm.test": "admin",
    "risk.assess": "member",
    "repo.access.manage": "admin",
    "wiki.configure": "admin",
    "wiki.import": "admin",
}
_RANK = {"viewer": 0, "member": 1, "admin": 2}


def authorize(
    db: Session, p: Principal, action: str, target: str = "", extra_check: str | None = None, **detail
) -> None:
    """Raise 403 unless the principal may perform `action`. `extra_check` is a pre-computed denial reason."""
    minimum = ACTION_MIN_ROLE.get(action)
    reason = None
    if minimum is None:
        reason = "unknown action"
    elif _RANK.get(p.role, -1) < _RANK[minimum]:
        reason = f"requires {minimum}"
    elif extra_check:
        reason = extra_check
    if reason:
        audit(db, p, action, target, allowed=False, reason=reason, **detail)
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Action '{action}' denied: {reason}")
    audit(db, p, action, target, allowed=True, **detail)
