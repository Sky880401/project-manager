from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SessionStart(BaseModel):
    session_id: str
    project_path: Optional[str] = None
    started_at: datetime


class SessionEnd(BaseModel):
    session_id: str
    ended_at: datetime
    exit_reason: str = "normal"   # normal / rate_limited / error
    input_tokens: Optional[int] = 0
    output_tokens: Optional[int] = 0
    turns: Optional[int] = 0
    notes: Optional[str] = None


class SessionOut(BaseModel):
    id: int
    session_id: str
    project_path: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    duration_seconds: Optional[int]
    exit_reason: Optional[str]
    input_tokens: int
    output_tokens: int
    turns: int
    model_config = {"from_attributes": True}


class RateLimitCreate(BaseModel):
    reset_at: Optional[datetime] = None
    message: Optional[str] = None


class RateLimitOut(BaseModel):
    id: int
    occurred_at: datetime
    reset_at: Optional[datetime]
    resolved_at: Optional[datetime]
    is_resolved: bool
    message: Optional[str]
    model_config = {"from_attributes": True}


class ResumeQueueCreate(BaseModel):
    task_id: Optional[int] = None
    project_path: Optional[str] = None
    description: str
    checkpoint: Optional[str] = None
    priority: Optional[int] = 0


class ResumeQueueOut(BaseModel):
    id: int
    task_id: Optional[int]
    project_path: Optional[str]
    description: str
    checkpoint: Optional[str]
    status: str
    queued_at: datetime
    resumed_at: Optional[datetime]
    priority: int
    model_config = {"from_attributes": True}


class ClaudeStatusOut(BaseModel):
    is_available: bool
    is_rate_limited: bool
    rate_limit_reset_at: Optional[datetime]
    minutes_until_reset: Optional[int]
    active_session: Optional[SessionOut]
    queue_count: int
    today_sessions: int
    today_tokens: int
