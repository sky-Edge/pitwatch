# -*- coding: utf-8 -*-
"""批量建项目 API。

- POST /batch-import/preview   上传清单 → 返回点列表 + 列映射 + 建议分组（**不落库**）
- POST /batch-import/commit    按用户分好的组 → 建项目/建测点/写第 0 期记录

分组由前端（用户手动区分）决定，服务端不猜 —— 现场"哪个点属于哪个基坑"
是人看着平面图定的，系统只能给个初始建议。
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.importers import importer
from backend.schemas.batch_project import BatchCommitRequest
from backend.services import batch_project_service

batch_import_router = APIRouter(prefix="/batch-import", tags=["batch-import"])


@batch_import_router.post("/preview")
def preview(file: UploadFile = File(...)):
    """解析清单文件，返回点列表（不落库）。

    遵循项目铁律：预览阶段不落库，用户确认后才在 commit 里写库。
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm / .xls 文件")
    raw = file.file.read()
    path = importer.save_upload(raw, file.filename)
    try:
        return batch_project_service.parse_point_sheet(path)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 —— 解析失败给用户友好提示
        raise HTTPException(status_code=422, detail=f"文件解析失败：{e}") from e


@batch_import_router.post("/commit")
def commit(payload: BatchCommitRequest, db: Session = Depends(get_db)):
    """按分组建项目、建测点、写初始值（第 0 期记录）。"""
    if not payload.groups:
        raise HTTPException(status_code=400, detail="没有要生成的分组")
    groups = [g.model_dump() for g in payload.groups]
    return batch_project_service.commit_groups(db, groups)
