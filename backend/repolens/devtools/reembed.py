"""Prepare the database for a different embedding model.

    python -m repolens.devtools.reembed [--dry-run]

Run it after changing REPOLENS_EMBEDDING_* (especially the dimension). On PostgreSQL it resizes every vector column to
the configured dimension (existing vectors cannot be converted, so they are cleared), rebuilds the HNSW index, clears
the answer memory, and marks every repository for a fresh index. On SQLite there is nothing to resize. Afterwards the
repositories re-embed themselves the next time they index (or use "Re-index" in the app)."""

import argparse

from sqlalchemy import text

from .. import db as dbm
from ..config import get_settings
from ..models import AnswerMemory, Base, EmbeddingType, Repository
from ..services import embedding_state


def run(dry: bool = False) -> list[str]:
    dbm.init_engine()
    s = get_settings()
    eng = dbm.get_engine()
    done: list[str] = []
    cols = [
        (t.name, c.name) for t in Base.metadata.sorted_tables for c in t.columns if isinstance(c.type, EmbeddingType)
    ]
    with eng.begin() as conn:
        if eng.dialect.name == "postgresql":
            for table, col in cols:
                stmt = f"ALTER TABLE {table} ALTER COLUMN {col} TYPE vector({s.embedding_dim}) USING NULL"
                done.append(stmt)
                if not dry:
                    conn.execute(text(f"DROP INDEX IF EXISTS ix_{table}_vec"))
                    conn.execute(text(stmt))
            done.append("CREATE INDEX ix_code_chunks_vec (hnsw, cosine)")
            if not dry:
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_code_chunks_vec ON code_chunks USING hnsw (embedding vector_cosine_ops)"
                    )
                )
        else:
            done.append("SQLite stores vectors as JSON: nothing to resize")
    if not dry:
        with dbm.session_factory()() as session:
            session.query(AnswerMemory).delete()
            n = 0
            for repo in session.query(Repository):
                repo.indexed_sha, repo.status = None, "pending"
                n += 1
            session.commit()
            for org in {r.org_id for r in session.query(Repository)}:
                embedding_state.record(session, org)
            done.append(f"marked {n} repositories for re-indexing; cleared the answer memory")
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print what would change")
    for line in run(ap.parse_args().dry_run):
        print(line)


if __name__ == "__main__":
    main()
