# -*- coding: utf-8 -*-
"""观测记录业务服务。

规则：
- 测点必须存在（404）。
- series_type 必须与测点 point_type 匹配。
- 防重：同一 (point, series_type, component, obs_date) 的【生效记录】不可重复录入（409）。
- 更正：填错时通过 RecordCorrection 生成新记录，旧记录 superseded_by_id 指向新记录，
  原值保留、可追溯；更正只针对生效记录。
- 原始数据不可改数值；修改仅限 remark / status（suspect 异常标记等）。
"""
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.observation_record import ObservationRecord
from backend.repositories import observation_repo
from backend.schemas.observation_record import RecordCorrection, RecordCreate, RecordUpdate
from backend.services.point_service import get_point


def _ensure_series_matches_point(point, series_type: str) -> None:
    allowed = {"settlement", "displacement"} if point.point_type == "both" else {point.point_type}
    if series_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"测点 {point.point_no} 类型为 {point.point_type}，不支持 {series_type} 观测",
        )


def create_record(db: Session, point_id: int, data: RecordCreate, source: str = "manual") -> ObservationRecord:
    point = get_point(db, point_id)
    _ensure_series_matches_point(point, data.series_type)

    if observation_repo.get_active_by_key(db, point_id, data.series_type, data.component, data.obs_date):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"测点 {point.point_no} {data.series_type}/{data.component} "
                f"在 {data.obs_date} 已有生效记录，禁止重复录入；如填错请使用更正"
            ),
        )
    return observation_repo.create(db, point_id, data, source=source)


def list_records(
    db: Session,
    point_id: int,
    series_type: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    active_only: bool = True,
) -> list[ObservationRecord]:
    get_point(db, point_id)
    return observation_repo.list_by_point(db, point_id, series_type, from_date, to_date, active_only)


def update_record(db: Session, record_id: int, data: RecordUpdate) -> ObservationRecord:
    rec = observation_repo.get(db, record_id)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"观测记录 {record_id} 不存在",
        )
    return observation_repo.update(db, rec, data)


def correct_record(db: Session, record_id: int, data: RecordCorrection) -> ObservationRecord:
    """更正：仅允许对生效记录更正；生成新记录并让旧记录指向它。"""
    rec = observation_repo.get(db, record_id)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"观测记录 {record_id} 不存在",
        )
    if rec.superseded_by_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"记录 {record_id} 已被更正替代，请基于生效记录操作",
        )
    return observation_repo.correct(db, rec, data.value_mm, data.remark)
