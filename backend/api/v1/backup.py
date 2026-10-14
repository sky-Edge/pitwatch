# -*- coding: utf-8 -*-
"""备份 / 还原 API。

- POST /backup                手动备份（生成完整快照 + 审计记录）
- GET  /backup/list           备份列表
- POST /backup/{id}/restore   还原（破坏性操作，需 confirm=true）
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.schemas.backup import BackupRecordRead
from backend.services import backup_service

backup_router = APIRouter(prefix="/backup", tags=["backup"])


@backup_router.post("", response_model=BackupRecordRead)
def create_backup(db: Session = Depends(get_db)):
    """手动备份。"""
    return backup_service.create_backup(db)


@backup_router.get("/list", response_model=list[BackupRecordRead])
def list_backups(db: Session = Depends(get_db)):
    """备份列表。"""
    return backup_service.list_backups(db)


@backup_router.post("/{record_id}/restore")
def restore_backup(record_id: int, confirm: bool = False, db: Session = Depends(get_db)):
    """还原指定备份。

    破坏性操作：要求 confirm=true 二次确认（前端弹窗后携带）。
    还原前自动对当前库做一次快照备份，可回退。
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="还原为破坏性操作，需 confirm=true 二次确认")
    return backup_service.restore_backup(db, record_id)
