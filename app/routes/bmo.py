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
# 可派工的 workspace（repo）清單；每個由一個 worker 認領
WORKSPACES = [w.strip() for w in os.getenv("BMO_WORKSPACES", "project-manager,stock_quant").split(",") if w.strip()]
WORKSPACE_LABELS = {"project-manager": "專案管理器", "stock_quant": "Stocker"}
DEFAULT_WORKSPACE = WORKSPACES[0] if WORKSPACES else "project-manager"
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
    workspace: Optional[str] = None
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
    workspace: str = "project-manager"
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
@router.get("/workspaces")
def list_workspaces():
    """可派工的 workspace 清單，供 LIFF 派工時選擇。"""
    return [{"key": w, "label": WORKSPACE_LABELS.get(w, w)} for w in WORKSPACES]


def _diff_lines(diff: Optional[str]) -> int:
    """估算變更量：diff 內以 + / - 開頭（非 +++/--- 檔頭）的行數。"""
    if not diff:
        return 0
    n = 0
    for ln in diff.split("\n"):
        if (ln.startswith("+") and not ln.startswith("+++")) or \
           (ln.startswith("-") and not ln.startswith("---")):
            n += 1
    return n


@router.get("/stats")
def bmo_stats(days: int = 14, db: Session = Depends(get_db)):
    """每個專案（workspace）的資源使用量與每日開發量，供 LIFF 圖表呈現。

    資源使用量：各 workspace 累計的 job 數與變更行數（diff +/- 行）。
    每日開發量：近 days 天，各 workspace 每天完成的 job 數。
    """
    from datetime import timedelta
    jobs = db.query(BmoJob).all()

    # 各 workspace 累計資源使用量
    per_ws: dict[str, dict] = {}
    for j in jobs:
        ws = j.workspace or DEFAULT_WORKSPACE
        s = per_ws.setdefault(ws, {"jobs": 0, "done": 0, "lines": 0})
        s["jobs"] += 1
        if j.status == "done":
            s["done"] += 1
        s["lines"] += _diff_lines(j.diff)

    resources = [
        {"workspace": ws, "label": WORKSPACE_LABELS.get(ws, ws), **s}
        for ws, s in sorted(per_ws.items(), key=lambda kv: -kv[1]["lines"])
    ]

    # 每日開發量（近 days 天，依完成日期計）
    today = _now().date()
    day_keys = [(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    daily = {d.isoformat(): 0 for d in day_keys}
    earliest = day_keys[0]
    for j in jobs:
        ts = j.finished_at or j.created_at
        if not ts:
            continue
        d = ts.date()
        if d < earliest:
            continue
        key = d.isoformat()
        if key in daily:
            daily[key] += 1

    return {
        "resources": resources,
        "daily": [{"date": k, "count": v} for k, v in daily.items()],
    }


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(data: JobCreate, db: Session = Depends(get_db)):
    if not data.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is empty")
    _check_user(data.id_token)
    ws = data.workspace if data.workspace in WORKSPACES else DEFAULT_WORKSPACE
    job = BmoJob(prompt=data.prompt.strip(), task_id=data.task_id, status="queued", workspace=ws)
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

    # 追到這條 review 鏈的根任務，取得「原始需求」（claude -p 無記憶，必須把上下文帶齊）
    root = parent
    seen = set()
    while root.parent_id and root.parent_id not in seen:
        seen.add(root.id)
        p = db.query(BmoJob).filter(BmoJob.id == root.parent_id).first()
        if not p:
            break
        root = p
    original = (root.prompt or "").strip()[:1500]
    prev_result = (parent.result or parent.error or "(上一輪無輸出)").strip()[:2500]

    prompt = (
        f"【原始任務 #{root.id}】\n{original}\n\n"
        f"【你上一輪（#{parent.id}）的回覆】\n{prev_result}\n\n"
        f"【你上一輪的程式碼變更 diff】\n```\n{(parent.diff or '(無，未改檔)')[:5000]}\n```\n\n"
        f"【我的新 comment】\n{data.comment.strip()}\n\n"
        f"請依「原始任務 + 我的 comment」在同一分支 `{parent.branch}` 上實際動工"
        f"（若需改檔就改檔、產生 diff），完成後簡短回報。"
    )
    job = BmoJob(prompt=prompt, task_id=parent.task_id, parent_id=parent.id,
                 branch=parent.branch, status="queued", workspace=parent.workspace)
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
                 task_id=src.task_id, parent_id=src.id, branch=src.branch, status="queued",
                 workspace=src.workspace)
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
def queued_jobs(workspace: Optional[str] = None,
                x_worker_token: Optional[str] = Header(None), db: Session = Depends(get_db)):
    _require_worker(x_worker_token)
    q = db.query(BmoJob).filter(BmoJob.status == "queued")
    # worker 只認領自己 workspace 的 job；未指定 workspace 的舊 worker 看全部（相容）
    if workspace:
        q = q.filter(BmoJob.workspace == workspace)
    return q.order_by(BmoJob.id.asc()).all()


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
