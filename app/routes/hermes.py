"""Hermes：把任務派給 bmo 上的 worker，用 Claude Code headless 執行。

流程：
  LIFF/LINE 建立 job(queued) → bmo worker 輪詢 /queued → claim(running)
  → 跑 `claude -p` → complete(done/error) → LINE 推播通知。

worker 專用端點（queued/claim/complete）需帶 X-Worker-Token，
值為環境變數 HERMES_WORKER_TOKEN；未設定時不啟用驗證（僅供本機測試）。
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import os
import logging
import requests

from pydantic import BaseModel
from app.database import get_db
from app.models.claude_usage import HermesJob
from app.services.line_push import push_to_all

router = APIRouter(prefix="/hermes", tags=["hermes"])
logger = logging.getLogger(__name__)

WORKER_TOKEN = os.getenv("HERMES_WORKER_TOKEN", "")
# 只有這些 LINE userId 能派工（逗號分隔）；留空＝不限制（僅供測試）
ALLOWED_USERS = [u.strip() for u in os.getenv("HERMES_ALLOWED_USERS", "").split(",") if u.strip()]
# LINE Login channel id，用來驗證 LIFF 的 id_token（= LIFF_ID 的前綴數字）
LINE_CHANNEL_ID = os.getenv("HERMES_LINE_CHANNEL_ID", "")


def _verify_line_user(id_token: str | None) -> str | None:
    """用 LINE verify 端點驗證 id_token，回傳真實 userId(sub)，失敗回 None。"""
    if not id_token or not LINE_CHANNEL_ID:
        return None
    try:
        r = requests.post(
            "https://api.line.me/oauth2/v2.1/verify",
            data={"id_token": id_token, "client_id": LINE_CHANNEL_ID},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning(f"LINE verify failed: {r.status_code} {r.text[:120]}")
            return None
        return r.json().get("sub")
    except Exception as e:
        logger.warning(f"LINE verify error: {e}")
        return None


def _now():
    return datetime.now(timezone.utc)


def _require_worker(token: Optional[str]):
    if WORKER_TOKEN and token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid worker token")


# --- Schemas ---
class JobCreate(BaseModel):
    prompt: str
    task_id: Optional[int] = None
    id_token: Optional[str] = None

class JobComplete(BaseModel):
    result: Optional[str] = None
    error: Optional[str] = None

class JobOut(BaseModel):
    id: int
    prompt: str
    task_id: Optional[int] = None
    status: str
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# --- 建立 / 查詢（給 LIFF）---
@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(data: JobCreate, db: Session = Depends(get_db)):
    if not data.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is empty")
    # 白名單：只有指定 LINE userId 能派工
    if ALLOWED_USERS:
        sub = _verify_line_user(data.id_token)
        if sub not in ALLOWED_USERS:
            raise HTTPException(status_code=403, detail="只有授權的 LINE 帳號能派工給 BMO")
    job = HermesJob(prompt=data.prompt.strip(), task_id=data.task_id, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs", response_model=List[JobOut])
def list_jobs(limit: int = 20, db: Session = Depends(get_db)):
    return db.query(HermesJob).order_by(HermesJob.id.desc()).limit(limit).all()


# --- worker 專用 ---
@router.get("/jobs/queued", response_model=List[JobOut])
def queued_jobs(x_worker_token: Optional[str] = Header(None), db: Session = Depends(get_db)):
    _require_worker(x_worker_token)
    return db.query(HermesJob).filter(HermesJob.status == "queued").order_by(HermesJob.id.asc()).all()


@router.post("/jobs/{job_id}/claim", response_model=JobOut)
def claim_job(job_id: int, x_worker_token: Optional[str] = Header(None), db: Session = Depends(get_db)):
    _require_worker(x_worker_token)
    job = db.query(HermesJob).filter(HermesJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "queued":
        raise HTTPException(status_code=409, detail=f"job already {job.status}")
    job.status = "running"
    job.started_at = _now()
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/complete", response_model=JobOut)
def complete_job(job_id: int, data: JobComplete,
                 x_worker_token: Optional[str] = Header(None), db: Session = Depends(get_db)):
    _require_worker(x_worker_token)
    job = db.query(HermesJob).filter(HermesJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job.status = "error" if data.error else "done"
    job.result = data.result
    job.error = data.error
    job.finished_at = _now()
    db.commit()
    db.refresh(job)

    # LINE 通知
    head = (job.prompt or "")[:40]
    if job.status == "done":
        snippet = (job.result or "")[-400:]
        msg = f"🤖 BMO 完成任務 #{job.id}\n「{head}」\n\n{snippet}"
    else:
        msg = f"⚠️ BMO 任務 #{job.id} 失敗\n「{head}」\n\n{(job.error or '')[:300]}"
    try:
        push_to_all(db, msg)
        job.notified = True
        db.commit()
    except Exception:
        pass
    return job
