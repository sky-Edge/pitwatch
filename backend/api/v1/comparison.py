# -*- coding: utf-8 -*-
"""逐期对比 API。

- GET /projects/{project_id}/comparison   项目全部测点逐期对比（报告数据源）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.services import comparison_service

comparison_router = APIRouter(prefix="/projects/{project_id}/comparison", tags=["comparison"])


@comparison_router.get("")
def project_comparison(project_id: int, db: Session = Depends(get_db)):
    return comparison_service.project_comparison(db, project_id)
