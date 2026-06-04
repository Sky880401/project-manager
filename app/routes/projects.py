from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import os

from app.database import get_db
from app.models.project import Project, Milestone, Task
from app.routes.bmo import _verify_line_user
from app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectOut, ProjectReorder,
    MilestoneCreate, MilestoneUpdate, MilestoneOut,
    TaskCreate, TaskUpdate, TaskOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])

# 管理者的 LINE userId（逗號分隔）；可看見所有人的資料與舊資料(owner_id=NULL)
ADMIN_USERS = [u.strip() for u in os.getenv("BMO_ADMIN_USER", "").split(",") if u.strip()]


class Caller:
    """一次 request 的呼叫者身分：owner_id 與是否為管理者。"""
    def __init__(self, owner_id: Optional[str], is_admin: bool):
        self.owner_id = owner_id
        self.is_admin = is_admin


def get_caller(
    authorization: Optional[str] = Header(None),
    x_line_id_token: Optional[str] = Header(None),
    x_line_user_id: Optional[str] = Header(None),
    x_guest_preview: Optional[str] = Header(None),
) -> Caller:
    """解析呼叫者身分。
    1. 若設定了 BMO_LINE_CHANNEL_ID，優先用 id_token 向 LINE 驗證取得真實 userId。
    2. 否則退而採用前端帶的 X-Line-User-Id（未驗證，僅供測試）。
    3. 完全沒有身分（桌機 dashboard / 本機）視為管理者，可管理全部資料。
    X-Guest-Preview=1 時，即使是管理者也強制以一般使用者身分檢視（前端預覽用）。
    """
    token = x_line_id_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    owner_id = _verify_line_user(token) or x_line_user_id

    guest_preview = x_guest_preview == "1"
    # 沒有任何 LINE 身分 → 桌機 dashboard / 本機，視為管理者
    is_admin = (owner_id is None) or (owner_id in ADMIN_USERS)
    if guest_preview:
        is_admin = False
    return Caller(owner_id=owner_id, is_admin=is_admin)


def _now():
    return datetime.now(timezone.utc)


def _visible(query, caller: Caller):
    """套用擁有者過濾：管理者看全部，一般使用者只看自己的。"""
    if caller.is_admin:
        return query
    return query.filter(Project.owner_id == caller.owner_id)

@router.get("/whoami")
def whoami(caller: Caller = Depends(get_caller)):
    """前端用：判斷目前使用者是否管理者，決定要不要顯示 BMO/Claude 等管理分頁。"""
    return {"is_admin": caller.is_admin, "owner_id": caller.owner_id}


# === Projects ===

def _get_owned_project(project_id: int, caller: Caller, db: Session, include_deleted: bool = False):
    """取得呼叫者有權限的專案，否則 404（避免越權存取他人專案）。"""
    q = db.query(Project).filter(Project.id == project_id)
    if include_deleted:
        q = q.filter(Project.deleted_at.isnot(None))
    else:
        q = q.filter(Project.deleted_at.is_(None))
    return _visible(q, caller).first()


@router.get("/", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    q = db.query(Project).filter(Project.deleted_at.is_(None))
    return _visible(q, caller).order_by(
        Project.order.asc(), Project.created_at.asc()
    ).all()


@router.post("/reorder", response_model=List[ProjectOut])
def reorder_projects(data: ProjectReorder, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    # 依傳入的 id 順序設定 order；只動自己有權限的專案
    for idx, pid in enumerate(data.ids):
        if _get_owned_project(pid, caller, db):
            db.query(Project).filter(Project.id == pid).update({"order": idx})
    db.commit()
    q = db.query(Project).filter(Project.deleted_at.is_(None))
    return _visible(q, caller).order_by(
        Project.order.asc(), Project.created_at.asc()
    ).all()


@router.get("/trash", response_model=List[ProjectOut])
def list_trash(db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    q = db.query(Project).filter(Project.deleted_at.isnot(None))
    return _visible(q, caller).all()


@router.post("/", response_model=ProjectOut, status_code=201)
def create_project(data: ProjectCreate, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    project = Project(owner_id=caller.owner_id, **data.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    project = _get_owned_project(project_id, caller, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, data: ProjectUpdate, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    project = _get_owned_project(project_id, caller, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    """軟刪除：設定 deleted_at，資料保留可還原"""
    project = _get_owned_project(project_id, caller, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.deleted_at = _now()
    db.commit()


@router.post("/{project_id}/restore", response_model=ProjectOut)
def restore_project(project_id: int, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    project = _get_owned_project(project_id, caller, db, include_deleted=True)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found in trash")
    project.deleted_at = None
    db.commit()
    db.refresh(project)
    return project


# === Milestones ===

@router.post("/{project_id}/milestones", response_model=MilestoneOut, status_code=201)
def create_milestone(project_id: int, data: MilestoneCreate, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    if not _get_owned_project(project_id, caller, db):
        raise HTTPException(status_code=404, detail="Project not found")
    milestone = Milestone(project_id=project_id, **data.model_dump())
    db.add(milestone)
    db.commit()
    db.refresh(milestone)
    return milestone


@router.patch("/{project_id}/milestones/{milestone_id}", response_model=MilestoneOut)
def update_milestone(project_id: int, milestone_id: int, data: MilestoneUpdate, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    if not _get_owned_project(project_id, caller, db):
        raise HTTPException(status_code=404, detail="Project not found")
    milestone = db.query(Milestone).filter(
        Milestone.id == milestone_id,
        Milestone.project_id == project_id,
        Milestone.deleted_at.is_(None),
    ).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(milestone, field, value)
    db.commit()
    db.refresh(milestone)
    return milestone


@router.delete("/{project_id}/milestones/{milestone_id}", status_code=204)
def delete_milestone(project_id: int, milestone_id: int, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    if not _get_owned_project(project_id, caller, db):
        raise HTTPException(status_code=404, detail="Project not found")
    milestone = db.query(Milestone).filter(
        Milestone.id == milestone_id,
        Milestone.project_id == project_id,
        Milestone.deleted_at.is_(None),
    ).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    milestone.deleted_at = _now()
    db.commit()


# === Tasks ===

@router.post("/{project_id}/tasks", response_model=TaskOut, status_code=201)
def create_task(project_id: int, data: TaskCreate, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    if not _get_owned_project(project_id, caller, db):
        raise HTTPException(status_code=404, detail="Project not found")
    task = Task(project_id=project_id, **data.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/{project_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(project_id: int, task_id: int, data: TaskUpdate, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    if not _get_owned_project(project_id, caller, db):
        raise HTTPException(status_code=404, detail="Project not found")
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.project_id == project_id,
        Task.deleted_at.is_(None),
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{project_id}/tasks/{task_id}", status_code=204)
def delete_task(project_id: int, task_id: int, db: Session = Depends(get_db), caller: Caller = Depends(get_caller)):
    if not _get_owned_project(project_id, caller, db):
        raise HTTPException(status_code=404, detail="Project not found")
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.project_id == project_id,
        Task.deleted_at.is_(None),
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.deleted_at = _now()
    db.commit()
