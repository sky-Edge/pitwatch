# -*- coding: utf-8 -*-
"""初始值/日期表 API。

- GET /projects/{project_id}/baselines     各测点初始值（V0）独立区块数据
- GET /projects/{project_id}/record-dates  全部观测日期 + 初始值日期（日期表标记）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.services import baseline_service

baseline_router = APIRouter(prefix="/projects/{project_id}/baselines", tags=["baseline"])
dates_router = APIRouter(prefix="/projects/{project_id}/record-dates", tags=["baseline"])


@baseline_router.get("")
def baselines(project_id: int, db: Session = Depends(get_db)):
    return baseline_service.project_baselines(db, project_id)


@dates_router.get("")
def record_dates(project_id: int, db: Session = Depends(get_db)):
    return baseline_service.project_record_dates(db, project_id)
