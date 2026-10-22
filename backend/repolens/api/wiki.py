import hashlib
import posixpath
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import ConfluenceConnection, Document, DriftAlert, Repository, WikiSource
from ..schemas import (
    ConfluenceConnectionIn,
    ConfluenceImportIn,
    ConfluencePreviewIn,
    ConnectorCheckIn,
    ConnectorImportIn,
    ImportUrlIn,
    ScheduleIn,
    SlackExportIn,
    WikiAskIn,
)
from ..security import encrypt_secret, redact_secrets, redact_stored
from ..services import connectors, drift
from ..services import knowledge as kb
from ..services import wiki as wk
from ..services import wiki_import as wi
from ..services.access import scope_for
from ..services.confluence import ConfluenceError, parse_confluence_url
from ..services.jira import JiraError, validate_base_url
from ..services.policy import authorize
from .repos import _get_repo, schedule_index

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


# ------------------------------------------------------------------------------------------------ Confluence connection
def _conn(db: Session, org_id: str) -> ConfluenceConnection | None:
    return db.scalar(select(ConfluenceConnection).where(ConfluenceConnection.org_id == org_id))


@router.get("/confluence/connection")
def get_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = _conn(db, p.org_id)
    if c is None:
        return {"connected": False}
    if p.role != "admin":  # members only learn that a connection exists
        return {"connected": True}
    return {"connected": True, "base_url": c.base_url, "deployment": c.deployment, "email": c.email}


@router.put("/confluence/connection")
def put_connection(
    body: ConfluenceConnectionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    authorize(db, p, "wiki.configure", body.base_url)
    try:
        base = validate_base_url(body.base_url)
    except JiraError as e:
        raise HTTPException(400, str(e).replace("Jira URL", "Confluence URL")) from e
    if body.deployment == "cloud" and not body.email:
        raise HTTPException(400, "Confluence Cloud needs the account email next to the API token")
    c = _conn(db, p.org_id)
    if c is None:
        c = ConfluenceConnection(org_id=p.org_id, base_url=base, secret_encrypted="")
        db.add(c)
    c.base_url, c.deployment, c.email = base, body.deployment, body.email
    c.secret_encrypted = encrypt_secret(body.token)  # encrypted at rest, never returned
    db.commit()
    return {"connected": True, "base_url": c.base_url, "deployment": c.deployment, "email": c.email}


@router.delete("/confluence/connection", status_code=204)
def delete_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "wiki.configure", "delete")
    c = _conn(db, p.org_id)
    if c:
        db.delete(c)
        db.commit()


@router.post("/confluence/test")
def test_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "wiki.configure", "test")
    try:
        me = wi.client_for_org(db, p.org_id).current_user()
    except ConfluenceError as e:
        return {"ok": False, "error": redact_secrets(str(e))}
    return {"ok": True, "user": me.get("displayName") or me.get("username") or me.get("email")}


def _resolve_root(db: Session, org_id: str, url: str) -> dict:
    """Parse the URL and confirm the connected account can actually read it (fails fast, before anything is created)."""
    try:
        ref = parse_confluence_url(url)
        client = wi.client_for_org(db, org_id)
        page: dict | None
        if ref.page_id:
            page = client.get_page(ref.page_id)
        elif ref.title and ref.space_key:
            page = client.find_page(ref.space_key, ref.title)
            if page is None:
                raise ConfluenceError(f"No page titled '{ref.title}' in space {ref.space_key}", 404)
        else:
            sp = client.space(ref.space_key)
            return {"kind": "space", "space_key": ref.space_key, "title": sp.get("name", ref.space_key), "page_id": ""}
    except ConfluenceError as e:
        if "not connected" in str(e):
            raise HTTPException(400, "Connect Confluence first (Settings → Wiki sources)") from e
        raise HTTPException(400 if e.status is None else 502, redact_secrets(str(e))) from e
    return {
        "kind": "page",
        "space_key": (page.get("space") or {}).get("key", ref.space_key),
        "title": page.get("title", ""),
        "page_id": page.get("id", ""),
    }


@router.post("/confluence/preview")
def preview(body: ConfluencePreviewIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "wiki.import", "preview")
    root = _resolve_root(db, p.org_id, body.url)
    return {**root, "suggested_mode": "tree" if root["kind"] == "page" else "space"}


def _space_url(client, key: str) -> str:
    return f"{client.root}/spaces/{key}" if client.deployment == "cloud" else f"{client.root}/display/{key}"


def _page_url(client, page_id: str, key: str) -> str:
    return (
        f"{client.root}/spaces/{key}/pages/{page_id}"
        if client.deployment == "cloud"
        else f"{client.root}/pages/viewpage.action?pageId={page_id}"
    )


@router.get("/confluence/spaces")
def confluence_spaces(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Spaces the connected account can see, each with the URL to import it."""
    authorize(db, p, "wiki.configure", "browse")
    try:
        client = wi.client_for_org(db, p.org_id)
        return [{**s, "import_url": _space_url(client, s["key"])} for s in client.spaces()]
    except ConfluenceError as e:
        raise HTTPException(502, str(e)) from None


@router.get("/confluence/spaces/{key}/pages")
def confluence_space_pages(key: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The page tree of a space (titles only), with an import URL for every page."""
    import re

    authorize(db, p, "wiki.configure", "browse")
    if not re.fullmatch(r"[\w~.-]{1,80}", key):
        raise HTTPException(400, "Invalid space key")
    try:
        client = wi.client_for_org(db, p.org_id)
        pages, truncated = client.page_tree(key)
    except ConfluenceError as e:
        raise HTTPException(502, str(e)) from None
    return {
        "space_url": _space_url(client, key),
        "truncated": truncated,
        "pages": [{**x, "import_url": _page_url(client, x["id"], key)} for x in pages],
    }


@router.post("/confluence/import", status_code=202)
def import_confluence(
    body: ConfluenceImportIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    """Creates a docs collection (restricted to admins by default, since Confluence content may be sensitive),
    then fetches and indexes the pages in the background."""
    authorize(db, p, "wiki.import", body.url, mode=body.mode)
    root = _resolve_root(db, p.org_id, body.url)
    mode = body.mode if body.mode != "auto" else ("tree" if root["kind"] == "page" else "space")
    if mode in ("page", "tree") and root["kind"] != "page":
        raise HTTPException(400, "That URL points at a space; use mode 'space'")
    base_name = body.name or f"Confluence: {root['title'] or root['space_key']}"
    name, n = base_name[:190], 1
    while db.scalar(select(Repository.id).where(Repository.org_id == p.org_id, Repository.name == name)):
        n += 1
        name = f"{base_name[:180]} ({n})"
    repo = Repository(org_id=p.org_id, name=name, source="docs", url="", status="indexing", visibility=body.visibility)
    db.add(repo)
    db.flush()
    src = WikiSource(
        org_id=p.org_id,
        repo_id=repo.id,
        kind="confluence",
        url=body.url,
        mode=mode,
        space_key=root["space_key"],
        root_page_id=root["page_id"],
        max_pages=body.max_pages,
        last_status="running",
        created_by=p.user_id,
    )
    db.add(src)
    db.commit()
    wi.schedule_source_sync(src.id)
    return {"source_id": src.id, "repo_id": repo.id, "mode": mode, "name": name, "visibility": body.visibility}


# ------------------------------------------------------------------------------------------------ sources
def _source_view(db: Session, s: WikiSource, repo: Repository) -> dict:
    pages = db.scalar(select(func.count()).select_from(Document).where(Document.repo_id == repo.id)) or 0
    return {
        "id": s.id,
        "repo_id": repo.id,
        "name": repo.name,
        "kind": s.kind,
        "url": s.url,
        "mode": s.mode,
        "space_key": s.space_key,
        "max_pages": s.max_pages,
        "pages": pages,
        "status": s.last_status,
        "repo_status": repo.status,
        "error": s.last_error,
        "last_synced_at": s.last_synced_at,
        "visibility": repo.visibility,
        "sync_interval_hours": s.sync_interval_hours,
        "next_sync_at": s.next_sync_at,
        "label": connectors.get(s.kind).describe(s.config or {}) if s.kind in connectors.REGISTRY else s.url,
    }


# ------------------------------------------------------------------------------------------------ drift
def _drift_rows(db: Session, p: Principal, status: str) -> list[dict]:
    scope = scope_for(db, p)
    drift.refresh(db, p.org_id)
    q = select(DriftAlert).where(DriftAlert.org_id == p.org_id).order_by(DriftAlert.updated_at.desc())
    if status != "all":
        q = q.where(DriftAlert.status == status)
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    return [
        {
            "id": a.id,
            "status": a.status,
            "repo_id": a.repo_id,
            "repo": names.get(a.repo_id, ""),
            "doc_repo_id": a.doc_repo_id,
            "doc_repo": names.get(a.doc_repo_id, ""),
            "doc_path": a.doc_path,
            "doc_title": a.doc_title,
            "symbols": a.symbols,
            "from_sha": a.from_sha[:10],
            "to_sha": a.to_sha[:10],
            "created_at": a.created_at,
            "updated_at": a.updated_at,
        }
        for a in db.scalars(q.limit(300))
        if scope.can(a.repo_id) and scope.can(a.doc_repo_id)
    ]


@router.get("/drift")
def list_drift(
    status: str = Query("open", pattern="^(open|dismissed|resolved|all)$"),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Pages that describe code which has changed since they were written."""
    return _drift_rows(db, p, status)


@router.get("/drift/count")
def drift_count(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"open": len(_drift_rows(db, p, "open"))}


@router.post("/drift/{alert_id}/dismiss")
def dismiss_drift(alert_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = db.get(DriftAlert, alert_id)
    scope = scope_for(db, p)
    if a is None or a.org_id != p.org_id or not scope.can(a.repo_id) or not scope.can(a.doc_repo_id):
        raise HTTPException(404, "Alert not found")
    authorize(db, p, "document.write", a.doc_path, op="dismiss-drift")
    a.status, a.resolved_at, a.resolved_by = "dismissed", datetime.now(UTC), p.user_id
    db.commit()
    return {"status": a.status}


# ------------------------------------------------------------------------------------------------ connectors
def _connector(kind: str):
    connectors.load_all()
    try:
        return connectors.get(kind)
    except connectors.ConnectorError:
        raise HTTPException(404, "Unknown source type") from None


@router.get("/connectors")
def list_connectors(p: Principal = Depends(current_principal)):
    """The kinds of source that can be connected, with the fields each one needs (secrets are write-only)."""
    connectors.load_all()
    return [
        {
            "kind": c.kind,
            "label": c.label,
            "description": c.description,
            "needs_secret": c.needs_secret,
            "fields": [
                {k: v for k, v in f.__dict__.items() if v not in ("", [], None) or k in ("required", "secret")}
                for f in c.fields
            ],
        }
        for c in connectors.REGISTRY.values()
    ]


@router.post("/connectors/{kind}/check")
def check_connector(
    kind: str, body: ConnectorCheckIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    authorize(db, p, "wiki.configure", kind, op="check")
    c = _connector(kind)
    try:
        config = c.validate(body.config)
        detail = c.check(connectors.Ctx(db, p.org_id, config, body.secret.strip(), 1, connectors.transports.get(kind)))
    except connectors.ConnectorError as e:
        return {"ok": False, "error": redact_secrets(str(e))}
    return {"ok": True, "detail": detail}


@router.post("/connectors/{kind}/import", status_code=202)
def import_connector(
    kind: str, body: ConnectorImportIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    """Create a collection fed by a connector. Content defaults to restricted visibility because tickets and chat
    can be sensitive."""
    authorize(db, p, "wiki.import", kind, op="connector")
    c = _connector(kind)
    try:
        config = c.validate(body.config)
    except connectors.ConnectorError as e:
        raise HTTPException(400, str(e)) from None
    secret = body.secret.strip()
    if c.needs_secret and not secret:
        raise HTTPException(400, "A token or key is required")
    base_name = body.name or c.describe(config)
    name, n = base_name[:190], 1
    while db.scalar(select(Repository.id).where(Repository.org_id == p.org_id, Repository.name == name)):
        n += 1
        name = f"{base_name[:180]} ({n})"
    repo = Repository(org_id=p.org_id, name=name, source="docs", url="", status="indexing", visibility=body.visibility)
    db.add(repo)
    db.flush()
    src = WikiSource(
        org_id=p.org_id,
        repo_id=repo.id,
        kind=kind,
        url=c.describe(config),
        mode="sync",
        max_pages=body.max_docs,
        config=config,
        secret_encrypted=encrypt_secret(secret) if secret else "",
        sync_interval_hours=body.sync_interval_hours,
        last_status="running",
        created_by=p.user_id,
    )
    db.add(src)
    db.commit()
    wi.schedule_source_sync(src.id)
    return {"source_id": src.id, "repo_id": repo.id, "name": name, "visibility": body.visibility}


@router.put("/sources/{source_id}/schedule")
def set_schedule(
    source_id: str, body: ScheduleIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    s, repo = _get_source(db, p, source_id)
    authorize(db, p, "wiki.import", repo.name, op="schedule")
    s.sync_interval_hours = body.sync_interval_hours
    s.next_sync_at = datetime.now(UTC) + timedelta(hours=body.sync_interval_hours) if body.sync_interval_hours else None
    db.commit()
    return {"sync_interval_hours": s.sync_interval_hours, "next_sync_at": s.next_sync_at}


@router.post("/slack-export", status_code=201)
def import_slack_export(body: SlackExportIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Import files from a Slack workspace export (channel folders of daily JSON files). Not synced afterwards."""
    from ..services.connectors.slack import export_to_docs

    authorize(db, p, "document.write", "slack-export", count=len(body.files))
    try:
        docs = export_to_docs([(f.path, f.content) for f in body.files])
    except connectors.ConnectorError as e:
        raise HTTPException(400, str(e)) from None
    if not docs:
        raise HTTPException(400, "No conversations found in those files")
    if body.collection_id:
        repo = _get_repo(db, p, body.collection_id)
        if repo.source != "docs":
            raise HTTPException(400, "Pick a docs collection")
    else:
        base = body.name or "Slack export"
        name, n = base[:190], 1
        while db.scalar(select(Repository.id).where(Repository.org_id == p.org_id, Repository.name == name)):
            n += 1
            name = f"{base[:180]} ({n})"
        repo = Repository(org_id=p.org_id, name=name, source="docs", url="", status="indexing", visibility="restricted")
        db.add(repo)
        db.flush()
    for d in docs:
        content, _ = redact_stored(d.markdown)
        h = hashlib.sha256(content.encode()).hexdigest()
        row = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == d.path))
        if row is None:
            db.add(
                Document(
                    org_id=p.org_id,
                    repo_id=repo.id,
                    path=d.path,
                    title=d.title[:500],
                    content=content,
                    content_hash=h,
                    created_by=p.user_id,
                )
            )
        elif row.content_hash != h:
            row.content, row.content_hash = content, h
    repo.status = "indexing"
    db.commit()
    schedule_index(repo.id)
    return {"repo_id": repo.id, "documents": len(docs)}


@router.get("/sources")
def list_sources(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    scope = scope_for(db, p)
    out = []
    for s in db.scalars(select(WikiSource).where(WikiSource.org_id == p.org_id).order_by(WikiSource.created_at.desc())):
        repo = db.get(Repository, s.repo_id)
        if repo is not None and scope.can(repo.id):
            out.append(_source_view(db, s, repo))
    return out


def _get_source(db: Session, p: Principal, source_id: str) -> tuple[WikiSource, Repository]:
    s = db.get(WikiSource, source_id)
    if s is None or s.org_id != p.org_id:
        raise HTTPException(404, "Source not found")
    return s, _get_repo(db, p, s.repo_id)


@router.post("/sources/{source_id}/sync", status_code=202)
def sync_source(source_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    s, repo = _get_source(db, p, source_id)
    authorize(db, p, "wiki.import", repo.name, op="sync")
    if s.last_status == "running":
        raise HTTPException(409, "A sync is already running")
    s.last_status, s.last_error = "running", None
    repo.status = "indexing"
    db.commit()
    wi.schedule_source_sync(s.id)
    return {"status": "scheduled"}


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(
    source_id: str, delete_pages: bool = False, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    s, repo = _get_source(db, p, source_id)
    authorize(db, p, "wiki.import", repo.name, op="delete", delete_pages=delete_pages)
    db.delete(s)
    db.commit()
    if delete_pages:
        from .repos import purge_repository

        purge_repository(db, repo)


# ------------------------------------------------------------------------------------------------ README / text URLs
@router.post("/import-url", status_code=201)
def import_url(body: ImportUrlIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Import a README / plain-text / HTML page from a public https URL (GitHub blob links become raw links)."""
    authorize(db, p, "document.write", body.url)
    if body.collection_id:
        repo = _get_repo(db, p, body.collection_id)
        if repo.source != "docs":
            raise HTTPException(400, "Pick a docs collection")
    else:
        if not body.name:
            raise HTTPException(422, "Provide collection_id or a name for a new collection")
        existing = db.scalar(select(Repository).where(Repository.org_id == p.org_id, Repository.name == body.name))
        if existing is None:
            repo = Repository(org_id=p.org_id, name=body.name, source="docs", url="", status="ready")
            db.add(repo)
            db.flush()
        elif existing.source != "docs" or not scope_for(db, p).can(existing.id):
            raise HTTPException(409, "A repository with that name already exists")
        else:
            repo = existing
    try:
        name, text, final_url = wi.fetch_text_url(body.url)
    except wi.FetchError as e:
        raise HTTPException(400, str(e)) from e
    content, redacted = redact_stored(text)
    path = posixpath.basename(name) or "page.txt"
    h = hashlib.sha256(content.encode()).hexdigest()
    row = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == path))
    if row is None:
        db.add(
            Document(
                org_id=p.org_id,
                repo_id=repo.id,
                path=path,
                title=path,
                content=content,
                content_hash=h,
                created_by=p.user_id,
                source_type="url",
                source_url=final_url,
                last_synced_at=datetime.now(UTC),
            )
        )
    else:
        row.content, row.content_hash, row.source_type = content, h, "url"
        row.source_url, row.last_synced_at = final_url, datetime.now(UTC)
    repo.status = "indexing"
    db.commit()
    schedule_index(repo.id)
    return {"collection_id": repo.id, "path": path, "source_url": final_url, "secrets_redacted": redacted}


# ------------------------------------------------------------------------------------------------ browse / analyse
@router.get("/overview")
def overview(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    scope = scope_for(db, p)
    data = wk.overview(db, p.org_id, scope)
    data["health"] = wk.health(db, p.org_id, scope, limit=0)["summary"]
    data["concepts"] = len(wk.concept_index(db, p.org_id, scope))
    return data


@router.get("/tree")
def tree(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _get_repo(db, p, repo_id)
    return wk.tree(db, p.org_id, scope_for(db, p), repo_id)


@router.get("/page")
def get_page(
    repo_id: str,
    path: str = Query(min_length=1, max_length=1000),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    _get_repo(db, p, repo_id)
    result = wk.page(db, p.org_id, scope_for(db, p), repo_id, path)
    if result is None:
        raise HTTPException(404, "Page not found")
    return result


@router.delete("/page", status_code=204)
def delete_page(
    repo_id: str,
    path: str = Query(min_length=1, max_length=1000),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Delete an uploaded/pasted/URL-imported page. Pages synced from Confluence are removed with their source."""
    from .knowledge import managed_by_sync

    repo = _get_repo(db, p, repo_id)
    if repo.source != "docs":
        raise HTTPException(400, "This page belongs to a code repository. Remove the repository to remove it.")
    authorize(db, p, "document.delete", path)
    doc = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == path))
    if doc is None:
        raise HTTPException(404, "Page not found")
    if managed_by_sync(doc):
        raise HTTPException(409, f"This page is synced from {doc.source_type}. Remove the source instead.")
    db.delete(doc)
    repo.status = "indexing"
    db.commit()
    schedule_index(repo.id)


@router.get("/concepts")
def concepts(
    min_docs: int = Query(2, ge=1, le=20),
    limit: int = Query(80, ge=1, le=300),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    return wk.concept_index(db, p.org_id, scope_for(db, p), min_docs=min_docs)[:limit]


@router.get("/concepts/detail")
def concept_detail(
    term: str = Query(min_length=2, max_length=120),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    result = wk.concept_detail(db, p.org_id, scope_for(db, p), term)
    if result is None:
        raise HTTPException(404, "No documentation mentions that term")
    return result


@router.get("/health")
def health(
    stale_days: int = Query(180, ge=7, le=3650),
    limit: int = Query(40, ge=1, le=200),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    return wk.health(db, p.org_id, scope_for(db, p), stale_days=stale_days, limit=limit)


@router.post("/ask")
def ask(body: WikiAskIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Ask the wiki: answers come from documentation first (cited), with the code they connect to listed alongside."""
    scope = scope_for(db, p)
    ids = scope.resolve(body.repo_ids)
    return kb.ask(db, p.org_id, body.query, ids or None, k=min(body.k, 10), scope=scope, kinds=["doc"])
