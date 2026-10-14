# -*- coding: utf-8 -*-
"""测点模块 API 路由。

边界：只做参数校验与转发，业务规则在 service 层。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.schemas.point import (
    BatchPointCreateRequest,
    BatchPointPreviewRequest,
    PointCreate,
    PointRead,
    PointUpdate,
)
from backend.services import hard_delete_service, point_service

# 项目下的测点集合
project_points_router = APIRouter(prefix="/projects/{project_id}/points", tags=["points"])

# 单个测点
point_router = APIRouter(prefix="/points", tags=["points"])


@project_points_router.get("", response_model=list[PointRead])
def list_points(
    project_id: int,
    include_inactive: bool = True,
    db: Session = Depends(get_db),
):
    """项目下的测点列表。include_inactive=false 仅返回在用测点。"""
    return point_service.list_points(db, project_id, include_inactive=include_inactive)


@project_points_router.post("", response_model=PointRead, status_code=201)
def create_point(
    project_id: int,
    payload: PointCreate,
    db: Session = Depends(get_db),
):
    """在项目下新建测点。"""
    return point_service.create_point(db, project_id, payload)


@project_points_router.post("/batch-preview")
def batch_preview_points(
    project_id: int,
    payload: BatchPointPreviewRequest,
    db: Session = Depends(get_db),
):
    """批量新建测点 · 预览校验（不落库）。"""
    return point_service.batch_preview_points(db, project_id, payload.items)


@project_points_router.post("/batch")
def batch_create_points(
    project_id: int,
    payload: BatchPointCreateRequest,
    db: Session = Depends(get_db),
):
    """批量新建测点 · 落库（仅收预览中 valid 的行，重复跳过并报告）。"""
    return point_service.batch_create_points(db, project_id, payload.items)


@point_router.get("/{point_id}", response_model=PointRead)
def get_point(
    point_id: int,
    db: Session = Depends(get_db),
):
    """测点详情。"""
    return point_service.get_point(db, point_id)


@point_router.put("/{point_id}", response_model=PointRead)
def update_point(
    point_id: int,
    payload: PointUpdate,
    db: Session = Depends(get_db),
):
    """更新测点（编号/类型/初始值/位置/状态）。"""
    return point_service.update_point(db, point_id, payload)


@point_router.delete("/{point_id}", response_model=PointRead)
def archive_point(
    point_id: int,
    db: Session = Depends(get_db),
):
    """停用测点（删除保护：不物理删除，历史数据保留）。"""
    return point_service.archive_point(db, point_id)


@point_router.delete("/{point_id}/hard", status_code=200)
def hard_delete_point(
    point_id: int,
    force: str | None = Query(None, description="确认串=测点编号，防止误删"),
    db: Session = Depends(get_db),
):
    """物理删除测点及其全部观测数据（force 必须等于测点编号；删除前自动备份）。"""
    return hard_delete_service.delete_point_hard(db, point_id, force)
