"""Delete a whole workspace (organisation) and everything in it: repositories and their index, wiki pages, investigations,
memory, settings, connections and people. For removing test workspaces and for honouring a deletion request.

    python -m repolens.devtools.delete_workspace --email admin@example.test            # shows what would be deleted
    python -m repolens.devtools.delete_workspace --email admin@example.test --yes      # deletes it

The workspace is found through any person's e-mail. This cannot be undone: take a backup first (docs/operations.md)."""

import argparse

from sqlalchemy import delete, func, select

from .. import db as dbm
from ..models import Base, Organization, Repository, User


def run(email: str, yes: bool = False) -> dict[str, int]:
    from ..api.repos import purge_repository

    dbm.init_engine()
    with dbm.session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            raise SystemExit(f"no person with the e-mail {email}")
        org = db.get(Organization, user.org_id)
        assert org is not None
        counts: dict[str, int] = {}
        for t in Base.metadata.sorted_tables:
            if "org_id" in t.c and t.name != "organizations":
                n = db.scalar(select(func.count()).select_from(t).where(t.c.org_id == org.id)) or 0
                if n:
                    counts[t.name] = n
        print(f"workspace '{org.name}' ({org.id}): " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        if not yes:
            print("nothing deleted; add --yes to delete it")
            return counts
        for repo in list(db.scalars(select(Repository).where(Repository.org_id == org.id))):
            purge_repository(db, repo)  # index, graph, clones and everything that references the repository
        user_ids = [u.id for u in db.scalars(select(User).where(User.org_id == org.id))]
        for t in reversed(Base.metadata.sorted_tables):
            if t.name in ("organizations", "users"):
                continue
            if "org_id" in t.c:
                db.execute(t.delete().where(t.c.org_id == org.id))
            elif "user_id" in t.c:
                db.execute(t.delete().where(t.c.user_id.in_(user_ids or ["-"])))
        db.execute(delete(User).where(User.org_id == org.id))
        db.delete(org)
        db.commit()
        print("deleted")
        return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    run(a.email, a.yes)


if __name__ == "__main__":
    main()
