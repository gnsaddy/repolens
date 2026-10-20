"""Draft, review and apply wiki edits. Nothing is written anywhere until a person approves a proposal.

Drafts come from two places: a drift alert (code changed under a page) produces an *update*; a confirmed investigation
produces a *new runbook page*. With a language model configured the draft is written by the model from the page, the
changed code and recent commits. Without one a deterministic template is used, which is honest about what it is: for
updates it only adds a review note listing what changed, it does not pretend to rewrite prose.
"""

import difflib
import hashlib
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Change, CodeChunk, DocProposal, Document, DriftAlert, Investigation, KGNode, Repository, WikiSource
from ..security import redact_secrets, redact_stored
from . import confluence as cf
from .llm import LLMClient, LLMUnavailable

log = logging.getLogger("repolens.proposals")
MAX_PAGE = 60_000
UPDATE_SYSTEM = (
    "You maintain engineering documentation. You are given one Markdown page, the code that changed since it was "
    "written, and recent commit messages. Return the COMPLETE updated page in Markdown and nothing else. Change only "
    "what the code changes require, keep the structure, headings, tone and any front matter, and never invent behaviour "
    "the code does not show. If nothing needs to change, return the page unchanged."
)
RUNBOOK_SYSTEM = (
    "You write incident runbooks for engineers. From the investigation below, write a concise Markdown runbook with "
    "the sections: Symptoms, Diagnosis, Fix, Verification, Prevention, References. Use only facts present in the "
    "investigation. Return Markdown only."
)


class ProposalError(Exception):
    pass


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def unified_diff(original: str, proposed: str, name: str = "page.md") -> str:
    return "".join(
        difflib.unified_diff(original.splitlines(True), proposed.splitlines(True), f"a/{name}", f"b/{name}", n=3)
    )


def _strip_fence(text: str) -> str:
    m = re.fullmatch(r"\s*```(?:markdown|md)?\n(.*?)\n```\s*", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def _llm(db: Session, org_id: str, workflow: str, ref: str) -> LLMClient | None:
    try:
        c = LLMClient(db, org_id, workflow=workflow, workflow_id=ref)
    except Exception:
        return None
    return None if c.offline else c


# ------------------------------------------------------------------------------------------------ page text
def page_text(db: Session, repo: Repository, path: str) -> str | None:
    if repo.source == "docs":
        d = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == path))
        return d.content if d else None
    from .wiki import _page_text

    return _page_text(db, repo, path)


# ------------------------------------------------------------------------------------------------ update from drift
def _changed_context(db: Session, alert: DriftAlert) -> tuple[str, list[str]]:
    code, paths = [], []
    for s in alert.symbols[:8]:
        node = db.get(KGNode, s["id"])
        chunk = db.get(CodeChunk, node.chunk_id) if node and node.chunk_id else None
        paths.append(s["path"])
        if chunk:
            code.append(f"### {s['qualname']} ({s['path']}:{chunk.start_line})\n```\n{chunk.content[:1500]}\n```")
    msgs = []
    for ch in db.scalars(
        select(Change).where(Change.repo_id == alert.repo_id).order_by(Change.committed_at.desc()).limit(40)
    ):
        if any(f.get("path") in paths for f in ch.files or []):
            msgs.append(f"- {ch.sha[:10]} {(ch.message or '').splitlines()[0][:120]}")
        if len(msgs) >= 5:
            break
    return "\n\n".join(code), msgs


def draft_update(db: Session, org_id: str, user_id: str, alert: DriftAlert) -> DocProposal:
    repo = db.get(Repository, alert.doc_repo_id)
    original = page_text(db, repo, alert.doc_path) if repo else None
    if repo is None or original is None:
        raise ProposalError("The page no longer exists")
    code, commits = _changed_context(db, alert)
    code_repo = db.get(Repository, alert.repo_id)
    names = ", ".join(f"`{s['qualname']}`" for s in alert.symbols[:8])
    llm = _llm(db, org_id, "wiki-proposal", alert.id)
    proposed, by = None, "template"
    if llm is not None:
        prompt = (
            f"PAGE ({alert.doc_path}):\n{original[:MAX_PAGE]}\n\nCHANGED CODE:\n{code or '(not available)'}\n\n"
            f"RECENT COMMITS:\n{chr(10).join(commits) or '(none)'}\n"
        )
        try:
            text = _strip_fence(llm.complete("doc-updater", UPDATE_SYSTEM, prompt, max_tokens=4000).text)
            if 0.5 * len(original) <= len(text) <= 3 * len(original) + 500:
                proposed, by = text, "llm"
            else:
                log.warning("discarding implausible model draft for %s", alert.doc_path)
        except (LLMUnavailable, Exception) as e:  # noqa: BLE001 - any model failure falls back to the template
            log.warning("model draft failed (%s); using the template", type(e).__name__)
    if proposed is None:
        note = (
            f"> **Review needed:** {names} changed in {code_repo.name if code_repo else 'the code'} ({alert.from_sha[:10]}…{alert.to_sha[:10]})"
            + (f", for example {commits[0][2:]}" if commits else "")
            + ". Check that this page still matches the code."
        )
        lines = original.splitlines()
        at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("# ")), 0)
        proposed = "\n".join([*lines[:at], "", note, *lines[at:]]).rstrip() + "\n"
    proposed, _ = redact_stored(proposed)
    rationale = f"{len(alert.symbols)} symbol(s) this page mentions changed: {names}."
    if by == "template":
        rationale += (
            " No AI model is connected, so this only adds a review note; connect one in Settings for a drafted rewrite."
        )
    p = DocProposal(
        org_id=org_id,
        kind="update",
        source="drift",
        source_ref=alert.id,
        doc_repo_id=alert.doc_repo_id,
        doc_path=alert.doc_path,
        title=alert.doc_title or alert.doc_path,
        original=original,
        proposed=proposed,
        rationale=rationale,
        base_hash=sha(original),
        drafted_by=by,
        created_by=user_id,
    )
    db.add(p)
    db.commit()
    return p


# ------------------------------------------------------------------------------------------------ runbook from incident
def _slug(text: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "incident")[:60]


def draft_runbook(db: Session, org_id: str, user_id: str, inv: Investigation, collection_id: str = "") -> DocProposal:
    if inv.root_cause_status != "confirmed":
        raise ProposalError("Confirm the root cause first, so runbooks are written from verified findings")
    top = inv.hypotheses[0] if inv.hypotheses else None
    refs = []
    for e in (top.evidence if top else [])[:8]:
        if e.get("type") == "code":
            refs.append(f"- Code: `{e['repo']}:{e['file']}` lines {e['start_line']}-{e['end_line']}")
        elif e.get("type") == "commit":
            refs.append(
                f"- Commit `{e['sha'][:10]}` by {e.get('author', '')}: {(e.get('message') or '').splitlines()[0][:100]}"
            )
        elif e.get("type") == "pr":
            refs.append(f"- Pull request #{e.get('number')}: {e.get('title', '')}")
    cause = (top.title if top else inv.root_cause) or inv.title
    facts = (
        f"Title: {inv.title}\nReported problem:\n{inv.query[:2500]}\n\nRoot cause: {cause}\n"
        f"Explanation: {(top.explanation if top else '')[:2000]}\nSuggested fix: {inv.suggested_fix[:1500]}\nEvidence:\n"
        + "\n".join(refs)
    )
    llm = _llm(db, org_id, "wiki-proposal", inv.id)
    body, by = None, "template"
    if llm is not None:
        try:
            body, by = _strip_fence(llm.complete("runbook-writer", RUNBOOK_SYSTEM, facts, max_tokens=2500).text), "llm"
        except Exception as e:  # noqa: BLE001
            log.warning("model runbook failed (%s); using the template", type(e).__name__)
    if body is None:
        body = "\n".join(
            [
                "## Symptoms",
                "",
                inv.query.strip()[:1200],
                "",
                "## Diagnosis",
                "",
                f"Confirmed root cause: **{cause}**",
                "",
                (top.explanation if top else "")[:1500],
                "",
                "## Fix",
                "",
                inv.suggested_fix.strip() or "See the evidence below.",
                "",
                "## Verification",
                "",
                "Re-run the failing scenario from the symptoms and confirm it no longer occurs.",
                "",
                "## Prevention",
                "",
                "Add a regression test that covers the failing case.",
                "",
                "## References",
                "",
                *(refs or ["- (no code evidence recorded)"]),
            ]
        )
    title = f"Runbook: {inv.title}"[:200]
    content, _ = redact_stored(
        f'---\ntitle: "{title}"\nsource: repolens-incident\ninvestigation: {inv.id}\n---\n\n# {title}\n\n{body.strip()}\n'
    )
    d = datetime.now(UTC).strftime("%Y%m%d")
    p = DocProposal(
        org_id=org_id,
        kind="new",
        source="incident",
        source_ref=inv.id,
        doc_repo_id=collection_id,
        doc_path=f"runbooks/{_slug(inv.title)}-{d}.md",
        title=title,
        original="",
        proposed=content,
        rationale="Written from a confirmed investigation."
        + (
            ""
            if by == "llm"
            else " No AI model is connected, so this is a structured template filled with the findings."
        ),
        base_hash="",
        drafted_by=by,
        created_by=user_id,
    )
    db.add(p)
    db.commit()
    return p


# ------------------------------------------------------------------------------------------------ apply
def _confluence_client(db: Session, org_id: str):
    from .wiki_import import client_for_org

    return client_for_org(db, org_id)


def apply(db: Session, p: DocProposal, user_id: str, target: str = "auto") -> str:
    """Write the approved change. Returns a human description of what was done; raises ProposalError otherwise."""
    from ..api.repos import schedule_index

    if p.status != "pending":
        raise ProposalError("This proposal was already decided")
    repo = db.get(Repository, p.doc_repo_id) if p.doc_repo_id else None
    if p.kind == "update":
        if repo is None:
            raise ProposalError("The page's collection no longer exists")
        doc = (
            db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == p.doc_path))
            if repo.source == "docs"
            else None
        )
        if repo.source != "docs":
            raise ProposalError("This page lives in a code repository. Copy the proposed text into a pull request.")
        if doc is None:
            raise ProposalError("The page no longer exists")
        if doc.source_type == "confluence":
            if target not in ("auto", "confluence_comment"):
                raise ProposalError("Pages synced from Confluence can only receive a comment")
            return _comment_on_confluence(db, p, doc)
        if doc.source_type not in ("upload", "url", ""):
            raise ProposalError(f"This page is synced from {doc.source_type} and cannot be edited here")
        if doc.content_hash != p.base_hash:
            raise ProposalError("The page changed after this draft was written. Reject it and draft again.")
        doc.content, doc.content_hash = p.proposed, sha(p.proposed)
        repo.status = "indexing"
        db.flush()
        schedule_index(repo.id)
        return f"Updated {repo.name}/{p.doc_path}"
    # new page: into the chosen collection, or a "Runbooks" collection created on demand
    if repo is None:
        repo = db.scalar(
            select(Repository).where(
                Repository.org_id == p.org_id, Repository.name == "Runbooks", Repository.source == "docs"
            )
        )
        if repo is None:
            repo = Repository(
                org_id=p.org_id, name="Runbooks", source="docs", url="", status="indexing", visibility="org"
            )
            db.add(repo)
            db.flush()
        p.doc_repo_id = repo.id
    if repo.source != "docs":
        raise ProposalError("Pick a documentation collection")
    path, n = p.doc_path, 1
    while db.scalar(select(Document.id).where(Document.repo_id == repo.id, Document.path == path)):
        n += 1
        path = p.doc_path[:-3] + f"-{n}.md"
    db.add(
        Document(
            org_id=p.org_id,
            repo_id=repo.id,
            path=path,
            title=p.title[:500],
            content=p.proposed,
            content_hash=sha(p.proposed),
            created_by=user_id,
        )
    )
    p.doc_path = path
    repo.status = "indexing"
    db.flush()
    schedule_index(repo.id)
    return f"Created {repo.name}/{path}"


def _comment_on_confluence(db: Session, p: DocProposal, doc: Document) -> str:
    src = db.scalar(select(WikiSource).where(WikiSource.repo_id == doc.repo_id, WikiSource.kind == "confluence"))
    if src is None or not doc.source_id:
        raise ProposalError("Could not find the Confluence page for this document")
    body = "\n".join(
        [
            "**RepoLens suggests an update to this page.**",
            "",
            p.rationale,
            "",
            "Proposed change:",
            "",
            "```diff",
            unified_diff(p.original, p.proposed, p.doc_path)[:6000],
            "```",
        ]
    )
    try:
        cid = _confluence_client(db, p.org_id).add_comment(doc.source_id, redact_secrets(body))
    except cf.ConfluenceError as e:
        raise ProposalError(str(e)) from None
    return f"Posted a comment on the Confluence page (comment {cid}). The page itself was not changed."


def decide(db: Session, p: DocProposal, user_id: str, approve: bool, target: str = "auto") -> DocProposal:
    if p.status != "pending":
        raise ProposalError("This proposal was already decided")
    now = datetime.now(UTC)
    if not approve:
        p.status, p.decided_by, p.decided_at = "rejected", user_id, now
        db.commit()
        return p
    try:
        p.result = apply(db, p, user_id, target)
        p.status = "applied"
    except ProposalError as e:
        p.status, p.result = "failed", str(e)
        p.decided_by, p.decided_at = user_id, now
        db.commit()
        raise
    p.decided_by, p.decided_at = user_id, now
    db.commit()
    return p
