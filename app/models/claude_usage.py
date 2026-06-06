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
    # 來自 Claude Code /usage 的真實額度百分比（無法從 transcript 推算，需手動回報）
    session_pct = Column(Integer, nullable=True)         # 5 小時 session 用量 %
    weekly_pct = Column(Integer, nullable=True)          # 當周用量 %
    usage_reported_at = Column(DateTime(timezone=True), nullable=True)  # /usage 最後回報時間
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
    kind = Column(String(20), default="task")       # task（跑 claude） / deploy（合併部署）
    workspace = Column(String(50), default="project-manager", index=True)  # 在哪個 repo 執行
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
    source = Column(Text, nullable=True)             # 派工來源；"hermes"=Hermes agent，NULL/其他=人類
    agent = Column(String(30), nullable=True, index=True)  # 專業角色 coding/test/pm/finance，NULL=預設 coding


class BmoJobSuggestion(Base):
    """Hermes agent 對某個 job 提交的「待採用建議」。

    Hermes 不能直接 comment（會觸發 worker 改碼），只能 suggest；
    真人在 LIFF review 後 adopt（轉成 comment 派工）或 reject。
    """
    __tablename__ = "bmo_job_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, nullable=False, index=True)   # FK -> bmo_jobs.id
    suggestion = Column(Text, nullable=False)              # 建議內容
    rationale = Column(Text, nullable=True)                # 理由（選填）
    status = Column(String(20), nullable=False, default="pending")  # pending/adopted/rejected
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)     # adopt/reject 時間
