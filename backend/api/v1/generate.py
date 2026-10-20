# -*- coding: utf-8 -*-
"""数据生成 API（需求 #9）。

- GET  /projects/{pid}/generate-data/preview?start=&end=
  生成前预检：范围内哪些日期已有数据、是真实数据还是之前自动生成的（只读）。
- POST /projects/{pid}/generate-data?start=&end=[&seed=&missing_rate=&on_conflict=]
  自动生成规范限值内的每日观测数据（source=generated）。
- POST /projects/{pid}/generate-data/cleanup  清理该项目的全部 generated 数据。
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.services import app_settings_service, data_generator_service
from backend.services.project_service import get_project

gen_router = APIRouter(prefix="/projects/{project_id}/generate-data", tags=["generate"])


@gen_router.get("/preview")
def preview_generation(
    project_id: int,
    start: date = Query(..., description="开始日期"),
    end: date = Query(..., description="结束日期"),
    db: Session = Depends(get_db),
):
    """生成前预检：哪些日期已经有数据（供界面询问用户"是否替换"）。"""
    get_project(db, project_id)
    return data_generator_service.preview_generation(db, project_id, start, end)


@gen_router.post("")
def generate_data(
    project_id: int,
    start: date = Query(..., description="开始日期"),
    end: date = Query(..., description="结束日期"),
    seed: int | None = Query(None, description="随机种子，固定后可复现"),
    missing_rate: float = Query(0.0, ge=0, le=0.5, description="每日缺测概率 0~0.5"),
    on_conflict: str = Query(
        data_generator_service.CONFLICT_SKIP,
        description="遇到已有数据：skip=跳过 / replace_generated=只替换自动生成的 / replace_all=连真实数据一起替换",
    ),
    instrument_mode: str | None = Query(
        None,
        description="观测模式：留空=标准（每点独立噪声）/ total_station=全站仪（含每次架站的公共误差）",
    ),
    station_centering_mm: float | None = Query(
        None, ge=0, le=20, description="全站仪模式：对中+整平误差极限值（mm），默认 2.0",
    ),
    station_direction_sec: float | None = Query(
        None, ge=0, le=60, description="全站仪模式：后视定向中误差（″），默认 5.0",
    ),
    station_distance_m: float | None = Query(
        None, ge=1, le=2000, description="全站仪模式：仪器到基坑的平均工作距离（m），默认 30",
    ),
    allow_warning: bool | None = Query(
        None,
        description="是否允许生成触发预警的读数；留空 = 按主界面那个全站开关",
    ),
    db: Session = Depends(get_db),
):
    """生成规范限值内的每日观测数据。

    `allow_warning` 留空时读主界面那个全站开关（`generate_allow_warning`）——
    关掉后生成的数据不会再出现预警/报警点。
    """
    get_project(db, project_id)
    if allow_warning is None:
        allow_warning = bool(app_settings_service.get_settings(db).get("generate_allow_warning", True))
    return data_generator_service.generate_series(
        db, project_id, start, end,
        seed=seed, missing_rate=missing_rate, on_conflict=on_conflict,
        instrument_mode=instrument_mode,
        station_centering_mm=station_centering_mm,
        station_direction_sec=station_direction_sec,
        station_distance_m=station_distance_m,
        allow_warning=allow_warning,
    )


@gen_router.post("/cleanup")
def cleanup_generated(project_id: int, db: Session = Depends(get_db)):
    """清理该项目全部 generated 数据（演示/测试用）。"""
    get_project(db, project_id)
    return data_generator_service.cleanup_generated(db, project_id)
