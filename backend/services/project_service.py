# -*- coding: utf-8 -*-
"""项目业务服务。

业务规则集中在此层：
- 编号唯一性校验（code 冲突 → 409）
- 删除保护（V1：项目一律走归档；物理删除需后续 Phase 引入"无观测数据"校验后开放）
"""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.project import Project
from backend.repositories import project_repo
from backend.schemas.project import ProjectCreate, ProjectUpdate


def list_projects(db: Session, include_inactive: bool = True) -> list[Project]:
    return project_repo.list_all(db, include_inactive=include_inactive)


def project_name_map(db: Session) -> dict[int, str]:
    """一次查出全部「项目 id → 名称」，供规则库/母版库标注归属。"""
    return {int(i): str(n) for i, n in db.execute(select(Project.id, Project.name))}


def get_project(db: Session, project_id: int) -> Project:
    project = project_repo.get(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_id} 不存在",
        )
    return project


def create_project(db: Session, data: ProjectCreate) -> Project:
    """新建项目，并把「默认规则套 / 默认母版」自动套上（可在主界面「项目默认值」里配）。

    为什么要自动套：新基坑进场时该"建完就能用" —— 建出来就有判预警的口径、
    就能直接导日报，不必每个项目再配一遍（用户 2026-10-11 反馈的繁琐点）。
    套用失败**不影响建项目本身**（默认值可能指向已被删掉的规则套），
    出问题只写进返回的 note 里，不把建项目这个主操作带崩。
    """
    if project_repo.get_by_code(db, data.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目编号 {data.code} 已存在",
        )
    project = project_repo.create(db, data)
    try:
        from backend.services.app_settings_service import apply_defaults_to_new_project

        apply_defaults_to_new_project(db, project.id)
        db.refresh(project)
    except Exception:  # noqa: BLE001
        # 默认值坏了不该拦住"建项目"这件正事
        db.rollback()
    return project


def update_project(db: Session, project_id: int, data: ProjectUpdate) -> Project:
    project = get_project(db, project_id)
    if data.code and data.code != project.code and project_repo.get_by_code(db, data.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目编号 {data.code} 已存在",
        )
    return project_repo.update(db, project, data)


def archive_project(db: Session, project_id: int) -> Project:
    """删除保护：归档而非物理删除，原始数据永久保留。"""
    project = get_project(db, project_id)
    return project_repo.soft_delete(db, project)
