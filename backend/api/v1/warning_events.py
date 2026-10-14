# -*- coding: utf-8 -*-
"""预警事件 / 数据质量 API。

- POST /projects/{pid}/warning-events/recompute   重算并重建预警事件（派生表整体重建）
- POST /projects/{pid}/warning-events/fix-values  一键修正触发预警的读数（原值保留）
- GET  /projects/{pid}/warning-events             查询事件（可按等级/日期过滤）
- GET  /projects/{pid}/quality-issues             数据质量检查（突变/缺测，只标记不删除）
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.models.warning_event import WarningEvent
from backend.services import warning_event_service, warning_fix_service
from backend.services.point_service import list_points
from backend.services.project_service import get_project

events_router = APIRouter(prefix="/projects/{project_id}/warning-events", tags=["warning-events"])
quality_router = APIRouter(prefix="/projects/{project_id}/quality-issues", tags=["quality"])


@events_router.post("/recompute")
def recompute_events(project_id: int, db: Session = Depends(get_db)):
    """重算全部测点并重建预警事件（派生表，幂等）。"""
    get_project(db, project_id)
    result = warning_event_service.recompute_project(db, project_id)
    return result


@events_router.post("/fix-values")
def fix_values(
    project_id: int,
    dry_run: bool = Query(True, description="只预检不落库（默认）"),
    include_control: bool = Query(True, description="是否连「预警」一起修，false = 只修报警"),
    db: Session = Depends(get_db),
):
    """一键修正触发预警的观测读数。

    ⚠ 这会**改观测数据**（走更正流程：旧值置 superseded 保留、新值 source=correct），
    所以 `dry_run` 默认为 True —— 必须先预检、由用户确认后再执行。
    执行完自动重建预警事件（派生表），页面等级立刻跟上。
    """
    get_project(db, project_id)
    if dry_run:
        return warning_fix_service.plan_fixes(db, project_id, include_control)

    result = warning_fix_service.apply_fixes(db, project_id, include_control)
    result["recompute"] = warning_event_service.recompute_project(db, project_id)
    return result


@events_router.get("")
def list_events(
    project_id: int,
    level: str | None = Query(None, pattern="^(control|alarm)$"),
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
):
    """查询预警事件。"""
    get_project(db, project_id)
    stmt = (
        select(WarningEvent)
        .where(WarningEvent.project_id == project_id)
        .order_by(WarningEvent.obs_date.desc(), WarningEvent.id.desc())
    )
    if level:
        stmt = stmt.where(WarningEvent.level == level)
    if from_date:
        stmt = stmt.where(WarningEvent.obs_date >= from_date)
    if to_date:
        stmt = stmt.where(WarningEvent.obs_date <= to_date)
    events = list(db.scalars(stmt))

    # 附带测点编号
    names = {p.id: p.point_no for p in list_points(db, project_id, include_inactive=True)}
    out = []
    for e in events:
        d = {
            "id": e.id,
            "project_id": e.project_id,
            "point_no": names.get(e.point_id, f"#{e.point_id}"),
            "series_type": e.series_type,
            "component": e.component,
            "obs_date": e.obs_date.isoformat(),
            "indicator": e.indicator,
            "level": e.level,
            "trigger_type": e.trigger_type,
            "value_mm": e.value_mm,
            "rate_mm_per_day": e.rate_mm_per_day,
            "message": e.message,
            "recompute_batch": e.recompute_batch,
            "created_at": e.created_at.isoformat(),
        }
        out.append(d)
    return out


@quality_router.get("")
def quality_issues(project_id: int, db: Session = Depends(get_db)):
    """数据质量检查（突变检测 + 缺测统计）。"""
    get_project(db, project_id)
    return warning_event_service.detect_quality_issues(db, project_id)
