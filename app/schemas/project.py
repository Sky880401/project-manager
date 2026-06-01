from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.models.project import ProjectStatus, TaskStatus, MilestoneStatus, TaskPriority


# --- Task Schemas ---
class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    milestone_id: Optional[int] = None
    priority: Optional[TaskPriority] = TaskPriority.medium
    order: Optional[int] = 0

class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    milestone_id: Optional[int] = None
    checkpoint: Optional[str] = None
    order: Optional[int] = None

class TaskOut(TaskBase):
    id: int
    project_id: int
    status: TaskStatus
    checkpoint: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# --- Milestone Schemas ---
class MilestoneBase(BaseModel):
    name: str
    description: Optional[str] = None
    order: Optional[int] = 0

class MilestoneCreate(MilestoneBase):
    pass

class MilestoneUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[MilestoneStatus] = None
    order: Optional[int] = None

class MilestoneOut(MilestoneBase):
    id: int
    project_id: int
    status: MilestoneStatus
    tasks: List[TaskOut] = []
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# --- Project Schemas ---
class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[ProjectStatus] = None

class ProjectOut(ProjectBase):
    id: int
    status: ProjectStatus
    milestones: List[MilestoneOut] = []
    tasks: List[TaskOut] = []
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
