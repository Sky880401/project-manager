"""BMO：把任務派給 bmo 上的 worker，用 Claude Code headless 執行。

流程：
  LIFF/LINE 建立 job(queued) → BMO worker 輪詢 /queued → claim(running)
  → 在獨立 git 分支跑 `claude -p` → complete(帶 branch/diff) → LINE 通知。

Review 迴圈：
  job 完成後 LIFF 顯示 diff，使用者對變更下 comment →
  POST /jobs/{id}/comment 建立後續 job（沿用同一分支、parent_id 指回）→
  worker 在同分支讀 comment 繼續修改。

worker 專用端點（queued/claim/complete）需帶 X-Worker-Token，
值為環境變數 BMO_WORKER_TOKEN；未設定時不啟用驗證（僅供本機測試）。
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
from app.models.claude_usage import BmoJob
from app.services.line_push import push_to_all

router = APIRouter(prefix="/bmo", tags=["bmo"])
logger = logging.getLogger(__name__)

WORKER_TOKEN = os.getenv("BMO_WORKER_TOKEN", "")
# 只有這些 LINE userId 能派工（逗號分隔）；留空＝不限制（僅供測試）
ALLOWED_USERS = [u.strip() for u in os.getenv("BMO_ALLOWED_USERS", "").split(",") if u.strip()]
# LINE Login channel id，用來驗證 LIFF 的 id_token（= LIFF_ID 的前綴數字）
LINE_CHANNEL_ID = os.getenv("BMO_LINE_CHANNEL_ID", "")


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


def _clean_title(prompt: Optional[str]) -> str:
    """取出可讀標題：延續任務的 prompt 是自動包裝文，抓出使用者真正的 comment。"""
    p = prompt or ""
    marker = "我對上述變更的 review comment：\n"
    if marker in p:
        rest = p.split(marker, 1)[1]
        line = rest.strip().split("\n", 1)[0].strip()
        if line:
            return line[:40]
    return p.strip().split("\n", 1)[0][:40]


def _line_plain(text: str) -> str:
    """把 markdown 清成 LINE 看得懂的純文字（去粗體/標題/項目符號/表格/程式碼框）。"""
    import re
    out = []
    for ln in (text or "").split("\n"):
        s = ln.rstrip()
        if re.match(r"^\s*```", s):           # 程式碼框圍欄
            continue
        if re.match(r"^\s*\|?\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?\s*$", s):  # 表格分隔線
            continue
        s = re.sub(r"^\s*#{1,6}\s*", "", s)    # 標題 #
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s) # 粗體
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", s)  # 斜體
        s = re.sub(r"^\s*[-*]\s+", "", s)      # 項目符號 - *
        s = s.replace("|", " ").replace("`", "")
        out.append(s.strip())
    # 收掉多餘空行
    cleaned = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _require_worker(token: Optional[str]):
    if WORKER_TOKEN and token != WORKER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid worker token")


def _check_user(id_token: Optional[str]):
    if ALLOWED_USERS:
        sub = _verify_line_user(id_token)
        if sub not in ALLOWED_USERS:
            raise HTTPException(status_code=403, detail="只有授權的 LINE 帳號能派工給 BMO")


# --- Schemas ---
class JobCreate(BaseModel):
    prompt: str
    task_id: Optional[int] = None
    id_token: Optional[str] = None

class JobComment(BaseModel):
    comment: str
    id_token: Optional[str] = None

class JobArchive(BaseModel):
    id_token: Optional[str] = None

class JobComplete(BaseModel):
    result: Optional[str] = None
    error: Optional[str] = None
    branch: Optional[str] = None
    diff: Optional[str] = None

class JobDeploy(BaseModel):
    id_token: Optional[str] = None

class JobOut(BaseModel):
    id: int
    prompt: str
    kind: str = "task"
    task_id: Optional[int] = None
    parent_id: Optional[int] = None
    branch: Optional[str] = None
    diff: Optional[str] = None
    status: str
    archived: bool = False
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
    _check_user(data.id_token)
    job = BmoJob(prompt=data.prompt.strip(), task_id=data.task_id, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/comment", response_model=JobOut, status_code=201)
def comment_job(job_id: int, data: JobComment, db: Session = Depends(get_db)):
    """對某個已完成 job 的變更下 review comment，建立沿用同分支的後續 job。"""
    if not data.comment.strip():
        raise HTTPException(status_code=400, detail="comment is empty")
    _check_user(data.id_token)
    parent = db.query(BmoJob).filter(BmoJob.id == job_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="job not found")

    prompt = (
        f"這是延續任務 #{parent.id} 的修改。你上一輪在分支 `{parent.branch}` 做的變更（diff）：\n"
        f"```\n{(parent.diff or '(無)')[:6000]}\n```\n\n"
        f"我對上述變更的 review comment：\n{data.comment.strip()}\n\n"
        f"請閱讀我的 comment，判斷是否需要進一步修改；若需要就在同一分支上修改。"
    )
    job = BmoJob(prompt=prompt, task_id=parent.task_id, parent_id=parent.id,
                 branch=parent.branch, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/deploy", response_model=JobOut, status_code=201)
def deploy_job(job_id: int, data: JobDeploy, db: Session = Depends(get_db)):
    """一鍵合併+部署：建立一個 kind=deploy 的 job，worker 會把該分支合併進 main 並部署。"""
    _check_user(data.id_token)
    src = db.query(BmoJob).filter(BmoJob.id == job_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="job not found")
    if not src.branch:
        raise HTTPException(status_code=400, detail="此任務沒有可部署的分支")
    job = BmoJob(prompt=f"合併並部署分支 {src.branch}", kind="deploy",
                 task_id=src.task_id, parent_id=src.id, branch=src.branch, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs", response_model=List[JobOut])
def list_jobs(limit: int = 20, include_archived: bool = False, db: Session = Depends(get_db)):
    q = db.query(BmoJob)
    if not include_archived:
        q = q.filter(BmoJob.archived == False)  # noqa: E712
    return q.order_by(BmoJob.id.desc()).limit(limit).all()


@router.post("/jobs/{job_id}/archive", response_model=JobOut)
def archive_job(job_id: int, data: JobArchive | None = None, db: Session = Depends(get_db)):
    """使用者標注完成：隱藏此 job，並把來源任務標記為 completed（從待辦移除）。"""
    if data is not None:
        _check_user(data.id_token)
    job = db.query(BmoJob).filter(BmoJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job.archived = True
    # 連帶把來源任務標記完成，讓它從待辦消失
    if job.task_id:
        from app.models.project import Task, TaskStatus
        task = db.query(Task).filter(Task.id == job.task_id,
                                     Task.deleted_at.is_(None)).first()
        if task:
            task.status = TaskStatus.completed
    db.commit()
    db.refresh(job)
    return job


# --- worker 專用 ---
@router.get("/jobs/queued", response_model=List[JobOut])
def queued_jobs(x_worker_token: Optional[str] = Header(None), db: Session = Depends(get_db)):
    _require_worker(x_worker_token)
    return db.query(BmoJob).filter(BmoJob.status == "queued").order_by(BmoJob.id.asc()).all()


@router.post("/jobs/{job_id}/claim", response_model=JobOut)
def claim_job(job_id: int, x_worker_token: Optional[str] = Header(None), db: Session = Depends(get_db)):
    _require_worker(x_worker_token)
    job = db.query(BmoJob).filter(BmoJob.id == job_id).first()
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
    job = db.query(BmoJob).filter(BmoJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job.status = "error" if data.error else "done"
    job.result = data.result
    job.error = data.error
    if data.branch:
        job.branch = data.branch
    if data.diff is not None:
        job.diff = data.diff
    job.finished_at = _now()
    # 部署成功 → 把「同一條分支」的所有 job 一起隱藏（整條 task→comment 迭代鏈），
    # 避免分支已合併刪除後，鏈上較早的 job 還留著失效的「合併並上線」按鈕
    if job.kind == "deploy" and job.status == "done":
        if job.branch:
            db.query(BmoJob).filter(BmoJob.branch == job.branch).update({"archived": True})
        job.archived = True
    db.commit()
    db.refresh(job)

    # LINE 通知（精簡：標題用真正的 comment，內容取結論開頭，純文字）
    head = _clean_title(job.prompt)
    if job.kind == "deploy" and job.status == "done":
        msg = f"🚀 BMO 已合併並上線 #{job.id}"
    elif job.status == "done":
        # OUTPUT_GUIDE 已讓 BMO 把「✅完成/結論」放開頭，取前 300 字即可
        snippet = _line_plain(job.result or "")[:300]
        msg = f"🤖 BMO #{job.id}「{head}」\n{snippet}"
    else:
        msg = f"⚠️ BMO #{job.id}「{head}」失敗\n{_line_plain(job.error or '')[:200]}"
    try:
        push_to_all(db, msg)
        job.notified = True
        db.commit()
    except Exception:
        pass
    return job
