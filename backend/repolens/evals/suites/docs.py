"""Document question answering, retrieval half: does the right page come back, in what position, and can the
system tell a question the documentation answers from one it cannot?"""

import tempfile
from pathlib import Path

from ...config import get_settings
from ..metrics import auc, mean, mrr, recall_at_k, wilson
from .corpus import TOPICS, UNANSWERABLE
from .embedders import HASH, EmbedderSpec

STYLES = ("exact", "paraphrase", "confusable")


def _setup(spec: EmbedderSpec):
    from ... import db
    from ...models import Document, Organization, Repository
    from ...services import indexer

    spec.apply()
    path = Path(tempfile.mkdtemp(prefix="repolens-docs-eval-")) / "eval.db"
    db.init_engine(f"sqlite:///{path}")
    db.create_all()
    s = db.session_factory()()
    org = Organization(name="eval")
    s.add(org)
    s.flush()
    repo = Repository(org_id=org.id, name="docs", source="docs", url="", status="pending")
    s.add(repo)
    s.flush()
    import hashlib

    for t in TOPICS:
        text = f"# {t.title}\n\n{t.body}\n"
        s.add(
            Document(
                org_id=org.id,
                repo_id=repo.id,
                path=t.path,
                title=t.title,
                content=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
    s.commit()
    indexer.index_repository(s, repo.id)
    return s, org.id


def run(spec: EmbedderSpec = HASH, rerank: str = "lexical", mode: str = "hybrid") -> dict:
    from ...services import retrieval
    from ...services.embeddings import Embedder

    s, org = _setup(spec)
    embedder = Embedder()
    ranks: dict[str, list[int | None]] = {st: [] for st in STYLES}
    top_scores_ok: list[float] = []
    cov_ok: list[float] = []
    for t in TOPICS:
        for st in STYLES:
            q = getattr(t, st)
            hits = retrieval.search_chunks(s, org, q, k=10, embedder=embedder, mode=mode, rerank=rerank)
            files = []
            for h in hits:  # first rank of the right page (a page can appear through several chunks)
                if h.chunk.file_path not in files:
                    files.append(h.chunk.file_path)
            ranks[st].append(files.index(t.path) + 1 if t.path in files else None)
            if st != "confusable" and hits:
                top_scores_ok.append(hits[0].score)
                cov_ok.append(retrieval.coverage(q, hits[0]))
    top_scores_no = []
    cov_no = []
    for q in UNANSWERABLE:
        hits = retrieval.search_chunks(s, org, q, k=10, embedder=embedder, mode=mode, rerank=rerank)
        top_scores_no.append(hits[0].score if hits else 0.0)
        cov_no.append(retrieval.coverage(q, hits[0]) if hits else 0.0)
    s.close()
    allr = [r for st in STYLES for r in ranks[st]]
    out: dict = {
        "embedder": spec.name,
        "pages": len(TOPICS),
        "questions": len(allr),
        "top1": round(recall_at_k(allr, 1), 3),
        "top3": round(recall_at_k(allr, 3), 3),
        "recall5": round(recall_at_k(allr, 5), 3),
        "mrr": round(mrr(allr), 3),
        "refusal_auc": round(auc(top_scores_ok, top_scores_no), 3),
        "coverage_auc": round(auc(cov_ok, cov_no), 3),
        "coverage_answerable_min": round(min(cov_ok), 2),
        "coverage_unanswerable_max": round(max(cov_no), 2),
        "mean_top_score_answerable": round(mean(top_scores_ok), 4),
        "mean_top_score_unanswerable": round(mean(top_scores_no), 4),
    }
    lo, hi = wilson(sum(1 for r in allr if r == 1), len(allr))
    out["top1_ci95"] = [round(lo, 3), round(hi, 3)]
    for st in STYLES:
        out[f"{st}_top1"] = round(recall_at_k(ranks[st], 1), 3)
        out[f"{st}_mrr"] = round(mrr(ranks[st]), 3)
    out["dimension"] = get_settings().embedding_dim
    return out
