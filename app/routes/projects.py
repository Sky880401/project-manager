from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone

from app.database import get_db
from app.models.project import Project, Milestone, Task
from app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectOut, ProjectReorder,
    MilestoneCreate, MilestoneUpdate, MilestoneOut,
    TaskCreate, TaskUpdate, TaskOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])

def _now():
    return datetime.now(timezone.utc)

# === Projects ===

@router.get("/", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.deleted_at.is_(None)).order_by(
        Project.order.asc(), Project.created_at.asc()
    ).all()


@router.post("/reorder", response_model=List[ProjectOut])
def reorder_projects(data: ProjectReorder, db: Session = Depends(get_db)):
    # 依傳入的 id 順序設定 order；未列到的維持在後面
    for idx, pid in enumerate(data.ids):
        db.query(Project).filter(Project.id == pid, Project.deleted_at.is_(None)).update({"order": idx})
    db.commit()
    return db.query(Project).filter(Project.deleted_at.is_(None)).order_by(
        Project.order.asc(), Project.created_at.asc()
    ).all()


@router.get("/trash", response_model=List[ProjectOut])
def list_trash(db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.deleted_at.isnot(None)).all()


@router.post("/", response_model=ProjectOut, status_code=201)
def create_project(data: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(**data.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(
        Project.id == project_id, Project.deleted_at.is_(None)
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, data: ProjectUpdate, db: Session = Depends(get_db)):
    project = db.query(Project).filter(
        Project.id == project_id, Project.deleted_at.is_(None)
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """軟刪除：設定 deleted_at，資料保留可還原"""
    project = db.query(Project).filter(
        Project.id == project_id, Project.deleted_at.is_(None)
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.deleted_at = _now()
    db.commit()


@router.post("/{project_id}/restore", response_model=ProjectOut)
def restore_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(
        Project.id == project_id, Project.deleted_at.isnot(None)
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found in trash")
    project.deleted_at = None
    db.commit()
    db.refresh(project)
    return project


# === Milestones ===

@router.post("/{project_id}/milestones", response_model=MilestoneOut, status_code=201)
def create_milestone(project_id: int, data: MilestoneCreate, db: Session = Depends(get_db)):
    if not db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first():
        raise HTTPException(status_code=404, detail="Project not found")
    milestone = Milestone(project_id=project_id, **data.model_dump())
    db.add(milestone)
    db.commit()
    db.refresh(milestone)
    return milestone


@router.patch("/{project_id}/milestones/{milestone_id}", response_model=MilestoneOut)
def update_milestone(project_id: int, milestone_id: int, data: MilestoneUpdate, db: Session = Depends(get_db)):
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
def delete_milestone(project_id: int, milestone_id: int, db: Session = Depends(get_db)):
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
def create_task(project_id: int, data: TaskCreate, db: Session = Depends(get_db)):
    if not db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).first():
        raise HTTPException(status_code=404, detail="Project not found")
    task = Task(project_id=project_id, **data.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/{project_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(project_id: int, task_id: int, data: TaskUpdate, db: Session = Depends(get_db)):
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
def delete_task(project_id: int, task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.project_id == project_id,
        Task.deleted_at.is_(None),
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.deleted_at = _now()
    db.commit()
