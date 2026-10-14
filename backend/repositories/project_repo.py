# -*- coding: utf-8 -*-
"""项目数据访问（repository）。

只做数据读写，不做业务规则判断（唯一性冲突、删除保护等放 service 层）。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.project import Project
from backend.schemas.project import ProjectCreate, ProjectUpdate


def get(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def get_by_code(db: Session, code: str) -> Project | None:
    return db.scalar(select(Project).where(Project.code == code))


def list_all(db: Session, include_inactive: bool = True) -> list[Project]:
    stmt = select(Project).order_by(Project.id)
    if not include_inactive:
        stmt = stmt.where(Project.is_active.is_(True))
    return list(db.scalars(stmt))


def create(db: Session, data: ProjectCreate) -> Project:
    project = Project(**data.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update(db: Session, project: Project, data: ProjectUpdate) -> Project:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


def soft_delete(db: Session, project: Project) -> Project:
    """归档：is_active=False，保留数据。"""
    project.is_active = False
    db.commit()
    db.refresh(project)
    return project
