# -*- coding: utf-8 -*-
"""备份 / 还原服务。

备份原理（SQLite）：
- 备份 = VACUUM INTO 生成完整快照文件（含 WAL 中未 checkpoint 的数据），
  输出到 database/backups/monitor_backup_YYYYmmdd_HHMMSS.db，并写 backup_records 审计。
- 还原 = sqlite3 online backup API 从备份文件覆盖主库（无需停服务、进程内完成）。

安全约束：
- 备份前先 WAL checkpoint(TRUNCATE) 保证快照完整；
- 还原是破坏性操作 —— API 层必须二次确认（confirm_token 或 confirm=True）；
- 还原前自动对当前库再做一次快照备份（防误操作可回退）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.backup_record import BackupRecord


def _db_path() -> Path:
    """当前主库文件路径（从 database_url 解析）。"""
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise HTTPException(status_code=500, detail="当前数据库非本地 SQLite，不支持文件备份")
    return Path(url.removeprefix("sqlite:///"))


def _checkpoint(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
    finally:
        con.close()


def create_backup(db: Session, note: str | None = None) -> BackupRecord:
    """手动备份：生成完整快照并记录。"""
    settings.ensure_dirs()
    src = _db_path()
    if not src.exists():
        raise HTTPException(status_code=400, detail="数据库文件不存在，无法备份")

    # 先 checkpoint 保证 WAL 数据写入主文件
    _checkpoint(src)

    # 文件名精确到毫秒，避免同秒多次备份撞名
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    dest = settings.backup_dir / f"monitor_backup_{ts}.db"
    n = 1
    while dest.exists():
        dest = settings.backup_dir / f"monitor_backup_{ts}_{n}.db"
        n += 1

    con = sqlite3.connect(src)
    try:
        # VACUUM INTO 生成干净、含全部数据的快照
        con.execute(f"VACUUM INTO '{dest.as_posix()}'")
    finally:
        con.close()

    if not dest.exists():
        raise HTTPException(status_code=500, detail="备份失败：未生成快照文件")

    rec = BackupRecord(
        filename=dest.name,
        size_bytes=dest.stat().st_size,
        backup_type="manual",
        note=note or f"手动备份 {datetime.now():%Y-%m-%d %H:%M:%S}",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def list_backups(db: Session) -> list[BackupRecord]:
    return list(db.scalars(select(BackupRecord).order_by(BackupRecord.id.desc())))


def restore_backup(db: Session, record_id: int) -> dict:
    """还原：用备份文件覆盖当前主库。

    前置保护：
    1. 记录必须存在且文件在磁盘上；
    2. 还原前自动对当前库做一次 pre-restore 快照备份（可回退）。
    """
    rec = db.get(BackupRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    backup_file = settings.backup_dir / rec.filename
    if not backup_file.exists():
        raise HTTPException(status_code=404, detail=f"备份文件缺失：{rec.filename}")

    src_path = _db_path()
    if not src_path.exists():
        raise HTTPException(status_code=400, detail="主库文件不存在")

    # 1) 还原前自动快照当前库（安全网）
    safety = create_backup(db, note=f"还原 {rec.filename} 前的自动快照")

    # 2) 校验备份文件完整性（可打开）
    try:
        check = sqlite3.connect(backup_file)
        row = check.execute("PRAGMA integrity_check").fetchone()
        check.close()
        if not row or row[0] != "ok":
            raise HTTPException(status_code=422, detail="备份文件完整性校验失败，已中止还原")
    except sqlite3.DatabaseError as e:
        raise HTTPException(status_code=422, detail=f"备份文件无法读取：{e}") from e

    # 3) online backup：备份文件 → 覆盖主库
    src_con = sqlite3.connect(backup_file)
    dst_con = sqlite3.connect(src_path)
    try:
        src_con.backup(dst_con)
    finally:
        src_con.close()
        dst_con.close()

    return {
        "restored": rec.filename,
        "safety_backup": safety.filename,
        "restored_at": datetime.now().isoformat(timespec="seconds"),
    }
