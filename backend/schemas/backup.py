# -*- coding: utf-8 -*-
"""备份记录 schema。"""
from datetime import datetime

from pydantic import BaseModel


class BackupRecordRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    filename: str
    size_bytes: int
    backup_type: str
    note: str | None
    created_at: datetime
