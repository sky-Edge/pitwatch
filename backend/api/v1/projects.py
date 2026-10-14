# -*- coding: utf-8 -*-
"""项目模块 API 路由。

边界：只做参数校验与转发，不含工程公式与业务规则（规则在 service 层）。
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from backend.services import (
    attention_service,
    hard_delete_service,
    project_brief_service,
    project_service,
    sample_project_service,
)

router = APIRouter(prefix="/projects", tags=["projects"])
brief_router = APIRouter(prefix="/project-briefs", tags=["projects"])
attention_router = APIRouter(prefix="/project-attention", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
def list_projects(
    include_inactive: bool = True,
    db: Session = Depends(get_db),
):
    """项目列表。include_inactive=false 时仅返回进行中项目。"""
    return project_service.list_projects(db, include_inactive=include_inactive)


@router.post("/sample", status_code=201)
def create_sample_project(
    start_date: date | None = Query(None, description="最后一期观测日，缺省为今天"),
    periods: int = Query(3, ge=2, le=30, description="观测期数（含初始值期）"),
    preset_key: str = Query("custom_8hao", description="套用的规范预设 key"),
    point_count: int = Query(8, ge=1, le=50, description="测点数量"),
    db: Session = Depends(get_db),
):
    """一键创建示例项目：项目 + 规范预设规则 + 测点 + 演示数据 + 预警事件。

    用于让新用户在几秒内看到完整效果，不必先建一遍项目。
    生成的观测数据 source=generated，可整批清理，不影响真实数据。
    """
    return sample_project_service.create_sample_project(
        db,
        start_date=start_date,
        periods=periods,
        preset_key=preset_key,
        point_count=point_count,
    )


@brief_router.get("")
def project_briefs(db: Session = Depends(get_db)):
    """全部项目的「一句结论」速览（列表页卡片用）。"""
    return project_brief_service.all_project_headlines(db)


@attention_router.get("")
def project_attention(db: Session = Depends(get_db)):
    """全部项目的「需要关注」汇总：数据质量 + 是否该观测。

    只读接口，不做任何写库操作（不触发预警重算）。
    """
    return attention_service.all_project_attention(db)


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
):
    """新建项目。"""
    return project_service.create_project(db, payload)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
):
    """项目详情。"""
    return project_service.get_project(db, project_id)


@router.put("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
):
    """更新项目（含 sign_config 正负号规则）。"""
    return project_service.update_project(db, project_id, payload)


@router.delete("/{project_id}", response_model=ProjectRead)
def archive_project(
    project_id: int,
    db: Session = Depends(get_db),
):
    """归档项目（删除保护：不物理删除，原始数据保留）。"""
    return project_service.archive_project(db, project_id)


@router.delete("/{project_id}/hard", status_code=200)
def hard_delete_project(
    project_id: int,
    force: str | None = Query(None, description="确认串=项目编号，防止误删"),
    db: Session = Depends(get_db),
):
    """物理删除项目及其全部数据（force 必须等于项目编号；删除前自动备份）。"""
    return hard_delete_service.delete_project_hard(db, project_id, force)


@router.get("/{project_id}/data-count")
def project_data_count(project_id: int, db: Session = Depends(get_db)):
    """项目数据量统计（删除确认弹窗展示用）。"""
    return hard_delete_service.count_project_data(db, project_id)
