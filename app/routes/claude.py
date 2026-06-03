from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone, timedelta
import os

# 5 小時視窗 output token 預算（與 LINE 顯示一致，可用環境變數覆蓋）
CODE_WINDOW_OUTPUT_BUDGET = int(os.getenv("CODE_WINDOW_OUTPUT_BUDGET", "500000"))
# 每日可用最大 token（今日用量百分比的分母，可用環境變數覆蓋）
DAILY_TOKEN_BUDGET = int(os.getenv("DAILY_TOKEN_BUDGET", "2000000"))

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
    window_earliest: datetime | None = None
    current_model: str | None = None


@router.post("/code-usage")
def report_code_usage(data: CodeUsageIn, db: Session = Depends(get_db)):
    # token 用量由 hook 解析 transcript 回報；/usage 的真實額度百分比無法從 transcript 取得，
    # 故沿用舊紀錄裡的 session_pct / weekly_pct，避免被覆寫清空。
    old = db.query(CodeUsageReport).order_by(CodeUsageReport.reported_at.desc()).first()
    session_pct = old.session_pct if old else None
    weekly_pct = old.weekly_pct if old else None
    usage_reported_at = old.usage_reported_at if old else None
    db.query(CodeUsageReport).delete()
    report = CodeUsageReport(
        **data.model_dump(),
        session_pct=session_pct,
        weekly_pct=weekly_pct,
        usage_reported_at=usage_reported_at,
    )
    db.add(report)
    db.commit()
    return {"status": "ok"}


class UsageLimitIn(BaseModel):
    session_pct: int | None = None  # /usage 顯示的 5 小時 session 用量 %
    weekly_pct: int | None = None   # /usage 顯示的當周用量 %


@router.post("/usage-limit")
def report_usage_limit(data: UsageLimitIn, db: Session = Depends(get_db)):
    """回報 Claude Code /usage 的真實額度百分比。
    無法從 transcript 推算，需從 /usage 畫面手動回報。"""
    report = db.query(CodeUsageReport).order_by(CodeUsageReport.reported_at.desc()).first()
    if not report:
        report = CodeUsageReport()
        db.add(report)
    if data.session_pct is not None:
        report.session_pct = data.session_pct
    if data.weekly_pct is not None:
        report.weekly_pct = data.weekly_pct
    report.usage_reported_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok", "session_pct": report.session_pct, "weekly_pct": report.weekly_pct}


@router.get("/code-usage")
def get_code_usage(db: Session = Depends(get_db)):
    report = db.query(CodeUsageReport).order_by(CodeUsageReport.reported_at.desc()).first()
    if not report:
        return {"available": False}
    used = report.window_5h_output or 0
    usage_pct = min(100, int(used / CODE_WINDOW_OUTPUT_BUDGET * 100)) if CODE_WINDOW_OUTPUT_BUDGET else 0
    # 今日總 token（輸入＋輸出）÷ 每日可用最大 token
    daily_used = (report.today_input or 0) + (report.today_output or 0)
    daily_pct = min(100, int(daily_used / DAILY_TOKEN_BUDGET * 100)) if DAILY_TOKEN_BUDGET else 0
    # 視窗重置時間 = 視窗內最早訊息 + 5 小時
    reset_at = None
    if report.window_earliest:
        e = report.window_earliest
        e = e.replace(tzinfo=timezone.utc) if e.tzinfo is None else e
        reset_at = (e + timedelta(hours=5)).isoformat()
    return {
        "available": True,
        "window_5h": {
            "input": report.window_5h_input,
            "output": report.window_5h_output,
            "cache_read": report.window_5h_cache_read,
            "cache_write": report.window_5h_cache_write,
            "messages": report.window_5h_messages,
        },
        "output_budget": CODE_WINDOW_OUTPUT_BUDGET,
        "usage_pct": usage_pct,
        "daily_budget": DAILY_TOKEN_BUDGET,
        "daily_used": daily_used,
        "daily_pct": daily_pct,
        "window_reset_at": reset_at,
        "today": {
            "input": report.today_input,
            "output": report.today_output,
            "messages": report.today_messages,
        },
        "current_model": report.current_model,
        # 來自 /usage 的真實額度百分比（優先顯示，沒有才退回 token 估算）
        "session_pct": report.session_pct,
        "weekly_pct": report.weekly_pct,
        "usage_reported_at": report.usage_reported_at.isoformat() if report.usage_reported_at else None,
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
