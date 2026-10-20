"""Repository-level access control.

Repositories are `org` (everyone in the organisation) or `restricted` (admins plus explicitly granted users).
A `Scope` is the set of repository ids a user may see; `None` means unrestricted. Every read path that can expose
repository content must go through a Scope — the helpers here are the single place that decides.
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RepoAccess, Repository, User

NOTHING = "-"  # sentinel id that matches no repository (an empty list would mean "everything" to search)


@dataclass(frozen=True)
class Scope:
    allowed: frozenset[str] | None  # None = unrestricted

    @property
    def restricted(self) -> bool:
        return self.allowed is not None

    def can(self, repo_id: str | None) -> bool:
        return self.allowed is None or (repo_id is not None and repo_id in self.allowed)

    def can_all(self, repo_ids) -> bool:
        return self.allowed is None or all(r in self.allowed for r in repo_ids)

    def resolve(self, requested: list[str] | None) -> list[str]:
        """Repository ids to search. Explicit ids outside the scope are a 404 (existence is not revealed)."""
        if requested:
            if not self.can_all(requested):
                raise HTTPException(404, "Repository not found")
            return list(requested)
        if self.allowed is None:
            return []
        return sorted(self.allowed) or [NOTHING]

    def ids_or_none(self) -> list[str] | None:
        """For SQL IN filters: None = no filter."""
        return None if self.allowed is None else (sorted(self.allowed) or [NOTHING])


UNRESTRICTED = Scope(None)


def scope_for_user(db: Session, org_id: str, user_id: str, role: str) -> Scope:
    if role == "admin":
        return UNRESTRICTED
    rows = db.execute(select(Repository.id, Repository.visibility).where(Repository.org_id == org_id)).all()
    restricted = {rid for rid, vis in rows if vis == "restricted"}
    if not restricted:
        return UNRESTRICTED  # fast path: nothing is restricted in this organisation
    granted = set(
        db.scalars(select(RepoAccess.repo_id).where(RepoAccess.user_id == user_id, RepoAccess.repo_id.in_(restricted)))
    )
    return Scope(frozenset({rid for rid, _ in rows if rid not in restricted} | granted))


def scope_for(db: Session, principal) -> Scope:
    return scope_for_user(db, principal.org_id, principal.user_id, principal.role)


def scope_for_user_id(db: Session, user_id: str) -> Scope:
    """For background jobs that only know who created them. Unknown users get nothing, never everything."""
    u = db.get(User, user_id) if user_id else None
    if u is None:
        return UNRESTRICTED if user_id in ("", "webhook", "eval-runner") else Scope(frozenset())
    return scope_for_user(db, u.org_id, u.id, u.role)


def repo_ids_in_evidence(inv) -> set[str]:
    """Every repository an investigation's stored evidence came from, plus the ones it was pointed at."""
    ids = set(inv.repo_ids or [])
    for h in inv.hypotheses:
        for e in h.evidence:
            if e.get("repo_id"):
                ids.add(e["repo_id"])
    return ids


def can_view_investigation(scope: Scope, inv, user_id: str) -> bool:
    """Visible when every repository the run touched is in the viewer's scope (re-checked on every read, so revoking a
    grant hides existing results immediately). Runs with no evidence yet are visible to their creator only."""
    if scope.allowed is None:
        return True
    touched = repo_ids_in_evidence(inv)
    if not touched:
        return inv.created_by == user_id
    return scope.can_all(touched)
