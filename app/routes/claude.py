from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone, timedelta

from app.database import get_db
from app.models.claude_usage import ClaudeSession, RateLimitEvent, ResumeQueue, CodeUsageReport
from pydantic import BaseModel
from app.schemas.claude_usage import (
    SessionStart, SessionEnd, SessionOut,
    RateLimitCreate, RateLimitOut,
    ResumeQueueCreate, ResumeQueueOut,
    ClaudeStatusOut,
)
from app.services.claude_monitor import get_claude_status, resolve_rate_limit, resume_next_in_queue

router = APIRouter(prefix="/claude", tags=["claude"])


# === Status ===

@router.get("/status", response_model=ClaudeStatusOut)
def claude_status(db: Session = Depends(get_db)):
    return get_claude_status(db)


# === Claude Code 訂閱用量 ===

class CodeUsageIn(BaseModel):
    window_5h_input: int = 0
    window_5h_output: int = 0
    window_5h_cache_read: int = 0
    window_5h_cache_write: int = 0
    window_5h_messages: int = 0
    today_input: int = 0
    today_output: int = 0
    today_messages: int = 0


@router.post("/code-usage")
def report_code_usage(data: CodeUsageIn, db: Session = Depends(get_db)):
    # 只保留最新一筆，刪除舊的
    db.query(CodeUsageReport).delete()
    report = CodeUsageReport(**data.model_dump())
    db.add(report)
    db.commit()
    return {"status": "ok"}


@router.get("/code-usage")
def get_code_usage(db: Session = Depends(get_db)):
    report = db.query(CodeUsageReport).order_by(CodeUsageReport.reported_at.desc()).first()
    if not report:
        return {"available": False}
    return {
        "available": True,
        "window_5h": {
            "input": report.window_5h_input,
            "output": report.window_5h_output,
            "cache_read": report.window_5h_cache_read,
            "cache_write": report.window_5h_cache_write,
            "messages": report.window_5h_messages,
        },
        "today": {
            "input": report.today_input,
            "output": report.today_output,
            "messages": report.today_messages,
        },
        "reported_at": report.reported_at.isoformat() if report.reported_at else None,
    }


# === Sessions ===

@router.post("/sessions/start", response_model=SessionOut, status_code=201)
def session_start(data: SessionStart, db: Session = Depends(get_db)):
    existing = db.query(ClaudeSession).filter(ClaudeSession.session_id == data.session_id).first()
    if existing:
        return existing
    session = ClaudeSession(**data.model_dump())
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.post("/sessions/end", response_model=SessionOut)
def session_end(data: SessionEnd, db: Session = Depends(get_db)):
    session = db.query(ClaudeSession).filter(ClaudeSession.session_id == data.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.ended_at = data.ended_at
    session.exit_reason = data.exit_reason
    session.input_tokens = data.input_tokens
    session.output_tokens = data.output_tokens
    session.turns = data.turns
    session.notes = data.notes

    if session.started_at and session.ended_at:
        start = session.started_at.replace(tzinfo=timezone.utc) if session.started_at.tzinfo is None else session.started_at
        end = session.ended_at.replace(tzinfo=timezone.utc) if session.ended_at.tzinfo is None else session.ended_at
        session.duration_seconds = int((end - start).total_seconds())

    db.commit()
    db.refresh(session)
    return session


@router.get("/sessions", response_model=List[SessionOut])
def list_sessions(limit: int = 20, db: Session = Depends(get_db)):
    return db.query(ClaudeSession).order_by(ClaudeSession.started_at.desc()).limit(limit).all()


# === Rate Limit ===

@router.post("/rate-limit", response_model=RateLimitOut, status_code=201)
def report_rate_limit(data: RateLimitCreate, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    reset_at = data.reset_at or (now + timedelta(hours=1))
    event = RateLimitEvent(reset_at=reset_at, message=data.message)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.post("/rate-limit/resolve", response_model=dict)
def resolve_rate_limit_endpoint(db: Session = Depends(get_db)):
    resolved = resolve_rate_limit(db)
    return {"resolved": resolved}


@router.get("/rate-limit/history", response_model=List[RateLimitOut])
def rate_limit_history(limit: int = 10, db: Session = Depends(get_db)):
    return db.query(RateLimitEvent).order_by(RateLimitEvent.occurred_at.desc()).limit(limit).all()


# === Resume Queue ===

@router.get("/queue", response_model=List[ResumeQueueOut])
def list_queue(db: Session = Depends(get_db)):
    return db.query(ResumeQueue).filter(
        ResumeQueue.status == "waiting"
    ).order_by(ResumeQueue.priority.desc(), ResumeQueue.queued_at.asc()).all()


@router.post("/queue", response_model=ResumeQueueOut, status_code=201)
def add_to_queue(data: ResumeQueueCreate, db: Session = Depends(get_db)):
    item = ResumeQueue(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.post("/queue/{item_id}/resume", response_model=ResumeQueueOut)
def mark_resumed(item_id: int, db: Session = Depends(get_db)):
    item = db.query(ResumeQueue).filter(ResumeQueue.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    item.status = "resumed"
    item.resumed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/queue/{item_id}", status_code=204)
def cancel_queue_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(ResumeQueue).filter(ResumeQueue.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    item.status = "cancelled"
    db.commit()
