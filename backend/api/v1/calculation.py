# -*- coding: utf-8 -*-
"""计算/预警查询 API（实时计算）。

- GET /points/{point_id}/computed?series_type=&component=  单测点单分量计算序列
- GET /projects/{project_id}/warning-status[?obs_date=]    项目预警状态总览
    · 不带 obs_date → 每项最新一期（项目列表"当前状态"）
    · 带 obs_date   → 该观测日的状态（观测记录页，保证与所选日期一致）
- GET /warning-summary                                    所有项目预警汇总（项目列表页）
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.services import calc_service
from backend.services.point_service import get_point

computed_router = APIRouter(prefix="/points/{point_id}/computed", tags=["calculation"])
status_router = APIRouter(prefix="/projects/{project_id}/warning-status", tags=["calculation"])
summary_router = APIRouter(prefix="/warning-summary", tags=["calculation"])


@computed_router.get("")
def computed_series(
    point_id: int,
    series_type: str,
    component: str,
    db: Session = Depends(get_db),
):
    point = get_point(db, point_id)
    return calc_service.compute_point_series(db, point, series_type, component)


@status_router.get("")
def warning_status(
    project_id: int,
    obs_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
):
    """预警状态总览。

    不带 `obs_date`：每项取最新一期（项目列表的"当前状态"）。
    带 `obs_date=YYYY-MM-DD`：取该观测日的状态（观测记录页用，保证
    表格里的预警标记与所选日期一致，不会"定位到 09-03 却显示 09-15 的等级"）。
    """
    return calc_service.compute_project_warning_status(db, project_id, obs_date=obs_date)


@summary_router.get("")
def warning_summary(db: Session = Depends(get_db)):
    return calc_service.all_projects_warning_summary(db)
