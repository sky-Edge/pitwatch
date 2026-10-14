# -*- coding: utf-8 -*-
"""观测记录 API。

- GET/POST  /points/{point_id}/records     列表 / 单点单日录入
- PUT       /records/{record_id}            仅改备注/状态（原始数据不可改数值）
- DELETE    /records/{record_id}            作废标记 void（不物理删除）
"""
from datetime import date

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.schemas.observation_record import (
    RecordCorrection,
    RecordCreate,
    RecordRead,
    RecordUpdate,
)
from backend.services import observation_service

records_router = APIRouter(prefix="/points/{point_id}/records", tags=["observation"])
record_router = APIRouter(prefix="/records", tags=["observation"])


@records_router.get("", response_model=list[RecordRead])
def list_records(
    point_id: int,
    series_type: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    return observation_service.list_records(db, point_id, series_type, from_date, to_date, active_only)


@records_router.post("", response_model=RecordRead, status_code=status.HTTP_201_CREATED)
def create_record(point_id: int, payload: RecordCreate, db: Session = Depends(get_db)):
    return observation_service.create_record(db, point_id, payload)


@record_router.put("/{record_id}", response_model=RecordRead)
def update_record(record_id: int, payload: RecordUpdate, db: Session = Depends(get_db)):
    return observation_service.update_record(db, record_id, payload)


@record_router.post("/{record_id}/correct", response_model=RecordRead)
def correct_record(record_id: int, payload: RecordCorrection, db: Session = Depends(get_db)):
    """更正：旧记录原值保留并指向新记录，新记录生效。"""
    return observation_service.correct_record(db, record_id, payload)


@record_router.delete("/{record_id}", response_model=RecordRead)
def void_record(record_id: int, db: Session = Depends(get_db)):
    """作废（不物理删除，保留原始数据）。"""
    return observation_service.update_record(db, record_id, RecordUpdate(status="void"))
