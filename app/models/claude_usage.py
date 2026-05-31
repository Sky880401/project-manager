from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float
from sqlalchemy.sql import func
from app.database import Base


class ClaudeSession(Base):
    __tablename__ = "claude_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), unique=True, index=True)
    project_path = Column(String(500))
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    exit_reason = Column(String(100))      # normal / rate_limited / error
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    turns = Column(Integer, default=0)
    notes = Column(Text)


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"

    id = Column(Integer, primary_key=True, index=True)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now())
    reset_at = Column(DateTime(timezone=True))    # 估算的重置時間
    resolved_at = Column(DateTime(timezone=True)) # 實際恢復時間
    is_resolved = Column(Boolean, default=False)
    message = Column(Text)


class ResumeQueue(Base):
    __tablename__ = "resume_queue"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, nullable=True)       # 對應 tasks 表
    project_path = Column(String(500))
    description = Column(Text, nullable=False)     # 要續接的任務描述
    checkpoint = Column(Text)                      # 中斷點內容
    status = Column(String(50), default="waiting") # waiting / resumed / cancelled
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    resumed_at = Column(DateTime(timezone=True))
    priority = Column(Integer, default=0)
