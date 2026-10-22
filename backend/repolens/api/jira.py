from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import Investigation, JiraConnection, JiraIssueLink, JiraPublishEvent
from ..schemas import JiraConnectionIn, JiraConnectionOut, JiraInvestigateIn, JiraPublishIn
from ..security import decrypt_secret, encrypt_secret, redact_secrets
from ..services import jira_analysis as ja
from ..services import knowledge as kb
from ..services.access import can_view_investigation, scope_for
from ..services.investigations import create_investigation, submit
from ..services.jira import JiraClient, JiraError, validate_base_url
from ..services.policy import authorize
from .investigations import get_inv, validate_repo_ids

router = APIRouter(prefix="/api/jira", tags=["jira"])

# Overridable in tests to inject a mock transport.
_transport = None


def _conn(db: Session, p: Principal) -> JiraConnection:
    c = db.scalar(select(JiraConnection).where(JiraConnection.org_id == p.org_id))
    if c is None:
        raise HTTPException(404, "Jira is not configured")
    return c


def _client(c: JiraConnection) -> JiraClient:
    return JiraClient(c.base_url, c.deployment, decrypt_secret(c.secret_encrypted), c.email, transport=_transport)


@router.get("/connection", response_model=JiraConnectionOut | None)
def get_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalar(select(JiraConnection).where(JiraConnection.org_id == p.org_id))


@router.put("/connection", response_model=JiraConnectionOut)
def put_connection(body: JiraConnectionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "jira.configure", body.base_url)
    try:
        base = validate_base_url(body.base_url)
    except JiraError as e:
        raise HTTPException(400, str(e)) from e
    if body.deployment == "cloud" and not body.email:
        raise HTTPException(400, "Jira Cloud requires the account email alongside the API token")
    c = db.scalar(select(JiraConnection).where(JiraConnection.org_id == p.org_id))
    if c is None:
        c = JiraConnection(org_id=p.org_id, base_url=base, secret_encrypted="")
        db.add(c)
    c.base_url, c.deployment, c.email = base, body.deployment, body.email
    c.secret_encrypted = encrypt_secret(body.token)  # never stored or returned in plaintext
    c.allow_publish, c.default_project = body.allow_publish, body.default_project
    try:  # best effort: remember whose token this is
        me = JiraClient(base, body.deployment, body.token, body.email, transport=_transport).myself()
        c.account = (me.get("displayName") or me.get("name") or "")[:200]
    except JiraError:
        c.account = ""
    db.commit()
    return c


@router.post("/check")
def check_credentials(body: JiraConnectionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Try the credentials typed into the form without saving them."""
    authorize(db, p, "jira.configure", "check")
    try:
        if body.deployment == "cloud" and not body.email:
            return {"ok": False, "error": "Jira Cloud needs the account email alongside the API token"}
        base = validate_base_url(body.base_url)
        me = JiraClient(base, body.deployment, body.token, body.email, transport=_transport).myself()
    except JiraError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "user": me.get("displayName") or me.get("name")}


@router.delete("/connection", status_code=204)
def delete_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "jira.configure", "disconnect")
    c = db.scalar(select(JiraConnection).where(JiraConnection.org_id == p.org_id))
    if c is not None:
        db.delete(c)
        db.commit()


@router.post("/test")
def test_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "jira.read", "test")
    conn = _conn(db, p)
    try:
        me = _client(conn).myself()
    except JiraError as e:
        return {"ok": False, "error": str(e)}
    name = me.get("displayName") or me.get("name")
    if name and name != conn.account:
        conn.account = name[:200]
        db.commit()
    return {"ok": True, "user": name}


@router.get("/projects")
def projects(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Projects the connected account can see, for the issue filter."""
    authorize(db, p, "jira.read", "projects")
    try:
        return _client(_conn(db, p)).projects()
    except JiraError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/issues")
def issues(
    jql: str = Query("statusCategory != Done AND issuetype = Bug ORDER BY created DESC"),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    authorize(db, p, "jira.read", "search")
    try:
        found = _client(_conn(db, p)).search(jql)
    except JiraError as e:
        raise HTTPException(502, str(e)) from e
    scope = scope_for(db, p)
    links = {}
    for lk in db.scalars(select(JiraIssueLink).where(JiraIssueLink.org_id == p.org_id)):
        inv = db.get(Investigation, lk.investigation_id)
        if inv is not None and can_view_investigation(scope, inv, p.user_id):  # never hint at hidden investigations
            links[lk.issue_key] = lk.investigation_id
    return [
        {
            "key": i.key,
            "summary": i.summary,
            "status": i.status,
            "type": i.issue_type,
            "priority": i.priority,
            "url": i.url,
            "investigation_id": links.get(i.key),
        }
        for i in found
    ]


@router.get("/issues/{key}")
def issue_detail(key: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The full ticket: description and every comment, for reading inside RepoLens."""
    authorize(db, p, "jira.read", key)
    try:
        i = _client(_conn(db, p)).get_issue(key)
    except JiraError as e:
        raise HTTPException(502, redact_secrets(str(e))) from e
    link = db.scalar(select(JiraIssueLink).where(JiraIssueLink.org_id == p.org_id, JiraIssueLink.issue_key == key))
    inv = db.get(Investigation, link.investigation_id) if link else None
    visible = inv is not None and can_view_investigation(scope_for(db, p), inv, p.user_id)
    return {
        "key": i.key,
        "summary": i.summary,
        "description": i.description,
        "status": i.status,
        "type": i.issue_type,
        "priority": i.priority,
        "labels": i.labels,
        "components": i.components,
        "reporter": i.reporter,
        "assignee": i.assignee,
        "created": i.created,
        "updated": i.updated,
        "url": i.url,
        "comments": i.comment_items,
        "investigation_id": link.investigation_id if visible and link else None,
    }


@router.post("/issues/{key}/analyze")
def analyze_issue(key: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Read the ticket and its comments: signals, what was tried, open questions, and what in the wiki/code relates."""
    authorize(db, p, "jira.read", key, op="analyze")
    try:
        issue = _client(_conn(db, p)).get_issue(key)
    except JiraError as e:
        raise HTTPException(502, redact_secrets(str(e))) from e
    digest = ja.analyze(issue)
    scope = scope_for(db, p)
    related = []
    if digest["search_terms"]:
        for r in kb.search(db, p.org_id, " ".join(digest["search_terms"][:8]), scope.ids_or_none(), k=6, scope=scope):
            related.append(
                {
                    "repo": r["repo"],
                    "repo_id": r["repo_id"],
                    "file": r["file"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "start_line": r["start_line"],
                    "node_id": r["node_id"],
                }
            )
    return {**digest, "related": related}


@router.post("/investigate", status_code=202)
def investigate(body: JiraInvestigateIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "investigation.create", body.issue_key, source="jira")
    repo_ids = validate_repo_ids(db, p, body.repo_ids)
    try:
        issue = _client(_conn(db, p)).get_issue(body.issue_key)
    except JiraError as e:
        raise HTTPException(502, str(e)) from e
    inv = create_investigation(
        db,
        p.org_id,
        p.user_id,
        ja.investigation_query(issue, ja.analyze(issue)),
        repo_ids,
        title=f"{issue.key}: {issue.summary}",
        jira_issue_key=issue.key,
    )
    db.add(JiraIssueLink(org_id=p.org_id, issue_key=issue.key, investigation_id=inv.id, summary=issue.summary))
    db.commit()
    submit(inv.id)
    return {"investigation_id": inv.id, "issue_key": issue.key}


@router.post("/publish")
def publish(body: JiraPublishIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    inv: Investigation = get_inv(db, p, body.investigation_id)
    conn = _conn(db, p)
    key = body.issue_key or inv.jira_issue_key
    denial = None
    if not body.approved:
        denial = "explicit approval required"
    elif not conn.allow_publish:
        denial = "publishing is disabled for this Jira connection"
    elif inv.status != "completed":
        denial = "investigation is not complete"
    elif not inv.verification.get("valid", False):
        denial = "report failed citation verification"
    elif not key:
        denial = "no Jira issue key"
    authorize(db, p, "jira.publish", key or "", extra_check=denial, investigation_id=inv.id)
    assert key  # guaranteed by the policy check above
    try:
        comment_id = _client(conn).add_comment(key, inv.report_md)
        status, err = "published", None
    except JiraError as e:  # the report stays stored locally either way
        comment_id, status, err = None, "failed", redact_secrets(str(e))
    db.add(
        JiraPublishEvent(
            org_id=p.org_id,
            investigation_id=inv.id,
            issue_key=key,
            approved_by=p.user_id,
            remote_comment_id=comment_id,
            status=status,
            error=err,
        )
    )
    if status == "published" and not inv.jira_issue_key:
        inv.jira_issue_key = key
    db.commit()
    if status == "failed":
        raise HTTPException(502, f"Publishing failed (report kept locally): {err}")
    return {"status": status, "comment_id": comment_id, "published_at": datetime.now(UTC)}


@router.get("/publish-events")
def publish_events(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(JiraPublishEvent)
        .where(JiraPublishEvent.org_id == p.org_id)
        .order_by(JiraPublishEvent.created_at.desc())
        .limit(50)
    )
    return [
        {
            "id": r.id,
            "investigation_id": r.investigation_id,
            "issue_key": r.issue_key,
            "status": r.status,
            "remote_comment_id": r.remote_comment_id,
            "error": r.error,
            "created_at": r.created_at,
        }
        for r in rows
    ]
