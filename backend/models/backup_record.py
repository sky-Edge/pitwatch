# -*- coding: utf-8 -*-
"""备份记录（审计）。
- backup_type：manual(手动) / auto(自动/定期)
- 备份文件保存在 database/backups/，记录文件名与大小便于管理。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    backup_type: Mapped[str] = mapped_column(String(16), default="manual")  # manual/auto
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
