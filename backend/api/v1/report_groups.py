# -*- coding: utf-8 -*-
"""合并报表组合 API。

一个组合 = 一份母版 + 主项目 + 「哪张数据表装哪个项目」。
出报接口在 `reports.py`（`/reports/merged/excel`、`/reports/merged/pdf`）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.schemas.report_group import (
    ReportGroupCreate,
    ReportGroupRead,
    ReportGroupUpdate,
)
from backend.services import report_group_service as svc

router = APIRouter(prefix="/report-groups", tags=["report-groups"])


def _boom(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=list[ReportGroupRead])
def list_groups(db: Session = Depends(get_db)):
    """全部合并报表组合。"""
    return [svc.serialize(db, g) for g in svc.list_groups(db)]


@router.get("/template-sheets")
def template_sheets(template_id: int = Query(..., description="母版 id"), db: Session = Depends(get_db)):
    """这份母版里有哪些数据表（配"表 ↔ 项目"时用）。"""
    try:
        return svc.template_sheet_options(db, template_id)
    except svc.GroupError as e:
        raise _boom(e) from e


@router.get("/{group_id}", response_model=ReportGroupRead)
def get_group(group_id: int, db: Session = Depends(get_db)):
    try:
        return svc.serialize(db, svc.get_group(db, group_id))
    except svc.GroupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("", response_model=ReportGroupRead, status_code=201)
def create_group(payload: ReportGroupCreate, db: Session = Depends(get_db)):
    """新建组合。"""
    try:
        return svc.serialize(db, svc.create_group(db, payload))
    except svc.GroupError as e:
        raise _boom(e) from e


@router.put("/{group_id}", response_model=ReportGroupRead)
def update_group(group_id: int, payload: ReportGroupUpdate, db: Session = Depends(get_db)):
    try:
        return svc.serialize(db, svc.update_group(db, group_id, payload))
    except svc.GroupError as e:
        raise _boom(e) from e


@router.delete("/{group_id}", status_code=204)
def delete_group(group_id: int, db: Session = Depends(get_db)):
    """删除组合（只删装订关系，不动任何观测数据）。"""
    try:
        svc.delete_group(db, group_id)
    except svc.GroupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
