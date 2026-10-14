# -*- coding: utf-8 -*-
"""图表数据 API —— Chart Data Builder。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.services import chart_data_service

chart_router = APIRouter(prefix="/projects/{project_id}/chart-data", tags=["chart"])


@chart_router.get("")
def chart_data(
    project_id: int,
    indicator: str = Query(..., pattern="^(settlement|displacement)$"),
    component: str | None = Query(None, pattern="^(value|X|Y)$"),
    db: Session = Depends(get_db),
):
    """多测点曲线数据（累计变化），含预警叠加点。"""
    return chart_data_service.build_chart_data(db, project_id, indicator, component)
