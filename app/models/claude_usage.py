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
    reset_at = Column(DateTime(timezone=True))
    resolved_at = Column(DateTime(timezone=True))
    is_resolved = Column(Boolean, default=False)
    message = Column(Text)
    notified = Column(Boolean, default=False)          # 限速通知已發送
    resolved_notified = Column(Boolean, default=False) # 解除通知已發送


class CodeUsageReport(Base):
    """Claude Code 訂閱用量回報（從 bmo transcript 解析）"""
    __tablename__ = "code_usage_reports"

    id = Column(Integer, primary_key=True, index=True)
    window_5h_input = Column(Integer, default=0)
    window_5h_output = Column(Integer, default=0)
    window_5h_cache_read = Column(Integer, default=0)
    window_5h_cache_write = Column(Integer, default=0)
    window_5h_messages = Column(Integer, default=0)
    today_input = Column(Integer, default=0)
    today_output = Column(Integer, default=0)
    today_messages = Column(Integer, default=0)
    window_earliest = Column(DateTime(timezone=True), nullable=True)
    current_model = Column(String(100), nullable=True)  # 最近一次使用的模型 id
    reported_at = Column(DateTime(timezone=True), server_default=func.now())


class LineUser(Base):
    __tablename__ = "line_users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(100), unique=True, index=True)
    display_name = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


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


class BmoJob(Base):
    """交給 bmo 上的 BMO worker 用 Claude Code headless 執行的任務。"""
    __tablename__ = "bmo_jobs"

    id = Column(Integer, primary_key=True, index=True)
    prompt = Column(Text, nullable=False)          # 要執行的內容
    task_id = Column(Integer, nullable=True)        # 來源 tasks.id（選填）
    parent_id = Column(Integer, nullable=True)      # 來源 job（review 迭代用）
    branch = Column(String(200))                    # 執行所在的 git 分支
    diff = Column(Text)                             # 該次變更的 git diff
    status = Column(String(20), default="queued")   # queued / running / done / error
    result = Column(Text)                            # 執行輸出
    error = Column(Text)                             # 錯誤訊息
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    notified = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)        # 使用者標注完成後隱藏
