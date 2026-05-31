from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models.claude_usage import ClaudeSession, RateLimitEvent, ResumeQueue


def get_active_session(db: Session) -> ClaudeSession | None:
    return db.query(ClaudeSession).filter(ClaudeSession.ended_at.is_(None)).first()


def get_current_rate_limit(db: Session) -> RateLimitEvent | None:
    return db.query(RateLimitEvent).filter(
        RateLimitEvent.is_resolved == False
    ).order_by(RateLimitEvent.occurred_at.desc()).first()


def get_claude_status(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    rate_limit = get_current_rate_limit(db)
    is_rate_limited = rate_limit is not None

    minutes_until_reset = None
    if rate_limit and rate_limit.reset_at:
        reset = rate_limit.reset_at.replace(tzinfo=timezone.utc) if rate_limit.reset_at.tzinfo is None else rate_limit.reset_at
        delta = (reset - now).total_seconds()
        minutes_until_reset = max(0, int(delta / 60))

    today_sessions = db.query(ClaudeSession).filter(
        ClaudeSession.started_at >= today_start
    ).count()

    today_tokens_result = db.query(ClaudeSession).filter(
        ClaudeSession.started_at >= today_start
    ).all()
    today_tokens = sum((s.input_tokens or 0) + (s.output_tokens or 0) for s in today_tokens_result)

    queue_count = db.query(ResumeQueue).filter(
        ResumeQueue.status == "waiting"
    ).count()

    return {
        "is_available": not is_rate_limited,
        "is_rate_limited": is_rate_limited,
        "rate_limit_reset_at": rate_limit.reset_at if rate_limit else None,
        "minutes_until_reset": minutes_until_reset,
        "active_session": get_active_session(db),
        "queue_count": queue_count,
        "today_sessions": today_sessions,
        "today_tokens": today_tokens,
    }


def resolve_rate_limit(db: Session) -> bool:
    rate_limit = get_current_rate_limit(db)
    if not rate_limit:
        return False
    now = datetime.now(timezone.utc)
    rate_limit.is_resolved = True
    rate_limit.resolved_at = now
    db.commit()
    return True


def resume_next_in_queue(db: Session) -> ResumeQueue | None:
    return db.query(ResumeQueue).filter(
        ResumeQueue.status == "waiting"
    ).order_by(ResumeQueue.priority.desc(), ResumeQueue.queued_at.asc()).first()
