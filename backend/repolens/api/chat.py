from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import ChatMessage, ChatThread, Repository
from ..schemas import ChatCreate, ChatMessageIn, ChatPatch
from ..services import chat as svc
from ..services.access import scope_for
from ..services.llm import LLMClient
from ..services.policy import authorize

router = APIRouter(prefix="/api/chats", tags=["chat"])


def _no_tokens(p: Principal) -> None:
    if p.token_scope:  # conversations belong to a person in the app; scripts and editors use search and ask
        raise HTTPException(403, "Chats are available when you are signed in, not with an access token")


def _own(db: Session, p: Principal, thread_id: str) -> ChatThread:
    t = db.get(ChatThread, thread_id)
    if t is None or t.org_id != p.org_id or t.user_id != p.user_id:
        raise HTTPException(
            404, "Chat not found"
        )  # someone else's chat looks exactly like a missing one, even to an admin
    return t


def _summary(t: ChatThread, last: ChatMessage | None = None, count: int = 0) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "repo_ids": t.repo_ids or [],
        "fallback": t.fallback,
        "archived": t.archived,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "messages": count,
        "preview": (last.content[:140] if last else ""),
    }


def _message(m: ChatMessage, visible: set[str] | None = None) -> dict:
    sources = [s for s in (m.sources or []) if visible is None or not s.get("repo_id") or s["repo_id"] in visible]
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "sources": sources,  # a source from a repository whose access was withdrawn is no longer shown
        "connections": m.connections or [],
        "mode": m.mode,
        "attachment": m.attachment or None,
        "created_at": m.created_at,
        "edited_at": m.edited_at,
    }


def _full(db: Session, p: Principal, t: ChatThread) -> dict:
    allowed = scope_for(db, p).allowed
    return {
        **_summary(t, t.messages[-1] if t.messages else None, len(t.messages)),
        "items": [_message(m, set(allowed) if allowed is not None else None) for m in t.messages],
    }


def _check_scope(db: Session, p: Principal, repo_ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys(repo_ids))
    if ids:
        found = set(db.scalars(select(Repository.id).where(Repository.org_id == p.org_id, Repository.id.in_(ids))))
        if found != set(ids) or not scope_for(db, p).can_all(ids):
            raise HTTPException(404, "Repository not found")
    return ids


@router.get("")
def list_chats(
    archived: bool = False,
    q: str = Query("", max_length=100),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    _no_tokens(p)
    stmt = select(ChatThread).where(
        ChatThread.org_id == p.org_id, ChatThread.user_id == p.user_id, ChatThread.archived == archived
    )
    rows = list(db.scalars(stmt.order_by(ChatThread.updated_at.desc()).limit(300)))
    needle = q.strip().lower()
    out = []
    for t in rows:
        last = t.messages[-1] if t.messages else None
        if needle and needle not in t.title.lower() and not any(needle in m.content.lower() for m in t.messages):
            continue
        out.append(_summary(t, last, len(t.messages)))
    return {"items": out, "model_available": not LLMClient(db, p.org_id, "chat", "").offline}


@router.post("", status_code=201)
def create_chat(body: ChatCreate, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _no_tokens(p)
    authorize(db, p, "chat.write", "new chat")
    if (
        db.scalar(
            select(func.count())
            .select_from(ChatThread)
            .where(ChatThread.org_id == p.org_id, ChatThread.user_id == p.user_id)
        )
        or 0
    ) >= svc.MAX_THREADS:
        raise HTTPException(409, f"You have {svc.MAX_THREADS} chats. Delete some you no longer need.")
    t = ChatThread(
        org_id=p.org_id,
        user_id=p.user_id,
        title=(body.title or "New chat").strip()[:200] or "New chat",
        repo_ids=_check_scope(db, p, body.repo_ids),
        fallback=body.fallback,
    )
    db.add(t)
    db.commit()
    return _full(db, p, t)


@router.get("/gaps")
def gaps(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Your own questions that your sources could not answer: the pages worth writing next."""
    _no_tokens(p)
    rows = db.scalars(
        select(ChatThread)
        .where(ChatThread.org_id == p.org_id, ChatThread.user_id == p.user_id)
        .order_by(ChatThread.updated_at.desc())
        .limit(svc.MAX_THREADS)
    )
    return {"items": svc.unanswered(list(rows))}


@router.get("/{thread_id}")
def get_chat(thread_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _no_tokens(p)
    return _full(db, p, _own(db, p, thread_id))


@router.patch("/{thread_id}")
def update_chat(
    thread_id: str, body: ChatPatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    _no_tokens(p)
    t = _own(db, p, thread_id)
    authorize(db, p, "chat.write", t.id, change="update")
    if body.title is not None:
        t.title = body.title.strip()[:200] or t.title
    if body.archived is not None:
        t.archived = body.archived
    if body.repo_ids is not None:
        t.repo_ids = _check_scope(db, p, body.repo_ids)
    if body.fallback is not None:
        t.fallback = body.fallback
    db.commit()
    return _full(db, p, t)


@router.delete("/{thread_id}", status_code=204)
def delete_chat(thread_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _no_tokens(p)
    t = _own(db, p, thread_id)
    authorize(db, p, "chat.write", t.id, change="delete")
    db.delete(t)
    db.commit()
    return Response(status_code=204)


def _reply(
    db: Session,
    p: Principal,
    t: ChatThread,
    text: str,
    attachment: svc.Attachment | None,
    before: list[ChatMessage],
    user_msg: ChatMessage,
) -> ChatMessage:
    out = svc.answer(db, p.org_id, scope_for(db, p), t, text, attachment, before)
    reply = ChatMessage(
        thread_id=t.id,
        org_id=p.org_id,
        role="assistant",
        content=out["content"],
        sources=out["sources"],
        connections=out["connections"],
        mode=out["mode"],
    )
    db.add(reply)
    t.updated_at = datetime.now(UTC)
    if t.title == "New chat" and not before:
        t.title = svc.title_for(text)
    db.commit()
    return reply


def _attachment(body: ChatMessageIn) -> svc.Attachment | None:
    if body.attachment is None:
        return None
    try:
        return svc.clean_attachment(body.attachment.name, body.attachment.text)
    except svc.ChatError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/{thread_id}/messages", status_code=201)
def send(thread_id: str, body: ChatMessageIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Ask a question in a chat; the answer is stored with it."""
    _no_tokens(p)
    t = _own(db, p, thread_id)
    authorize(db, p, "chat.write", t.id, change="message")
    if len(t.messages) >= svc.MAX_MESSAGES:
        raise HTTPException(409, "This chat is long. Start a new chat to keep answers focused.")
    att = _attachment(body)
    before = list(t.messages)
    um = ChatMessage(
        thread_id=t.id,
        org_id=p.org_id,
        role="user",
        content=body.content.strip(),
        attachment={"name": att.name, "chars": len(att.text)} if att else {},
        attachment_text=att.text if att else "",
    )
    db.add(um)
    t.archived = False  # writing in a chat brings it back from the archive
    db.flush()
    reply = _reply(db, p, t, um.content, att, before, um)
    return {**_summary(t, reply, len(t.messages)), "items": [_message(um), _message(reply)]}


@router.put("/{thread_id}/messages/{message_id}")
def edit_message(
    thread_id: str,
    message_id: str,
    body: ChatMessageIn,
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Change a question you asked. Everything after it is removed and the question is answered again."""
    _no_tokens(p)
    t = _own(db, p, thread_id)
    authorize(db, p, "chat.write", t.id, change="edit")
    msgs = list(t.messages)
    idx = next((i for i, m in enumerate(msgs) if m.id == message_id), None)
    if idx is None or msgs[idx].role != "user":
        raise HTTPException(404, "Message not found")
    target = msgs[idx]
    for m in msgs[idx + 1 :]:
        db.delete(m)
    new_att = _attachment(body)
    target.content = body.content.strip()
    if body.attachment is not None:  # a new file replaces the old one; none keeps what was attached
        target.attachment = {"name": new_att.name, "chars": len(new_att.text)} if new_att else {}
        target.attachment_text = new_att.text if new_att else ""
    target.edited_at = datetime.now(UTC)
    db.flush()
    keep = svc.Attachment(target.attachment["name"], target.attachment_text) if target.attachment else None
    reply = _reply(db, p, t, target.content, keep, msgs[:idx], target)
    db.refresh(t)
    return {**_summary(t, reply, len(t.messages)), "items": [_message(m) for m in t.messages]}
