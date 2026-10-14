# -*- coding: utf-8 -*-
"""导出文件检查 API —— 主界面「文件检查」。

- GET  /audit/status      配了哪个文件夹、上次什么时候查的、该不该提醒补扫
- POST /audit/run         扫那个文件夹，把导出的日报和系统逐项比一遍
- GET  /fs/dirs           目录浏览（只列目录名，用来"选文件夹"）

用户 2026-10-12：「我想让系统检查我导出的文件…检查主要是 变化速率 是否和系统中的同步、
变化速率是否超过预警规则」

⚠ 谁去读文件：**后端直接进文件夹扫**。不是浏览器把整个文件夹传上来 ——
现场一个文件夹里可能堆着几百份 1MB 多的日报，走浏览器就是几百 MB 的搬运，
而且浏览器拿不到绝对路径，"每周自动补扫"就没法成立。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.services import app_settings_service, export_audit_service

audit_router = APIRouter(tags=["audit"])

#: 目录浏览最多列多少个（巨型文件夹不至于把响应撑爆）
_MAX_DIRS = 400


class AuditRunPayload(BaseModel):
    """开始检查。folder 缺省 = 用上次存的那个；save=True 表示把 folder 记下来。"""

    folder: str | None = None
    save: bool = False


@audit_router.get("/audit/status")
def audit_status(db: Session = Depends(get_db)):
    """检查现状：文件夹、上次检查时间、要不要提醒补扫。"""
    return export_audit_service.folder_status(db)


@audit_router.post("/audit/run")
def audit_run(payload: AuditRunPayload, db: Session = Depends(get_db)):
    """扫文件夹并逐项比对。跑完记一次时间（"每周自动补扫"靠它判断）。"""
    folder = (payload.folder or "").strip()
    if not folder:
        folder = str(app_settings_service.get_settings(db).get("audit_folder") or "")
    if not folder:
        raise HTTPException(status_code=422, detail="还没选要检查的文件夹 —— 请先选一个（导出日报都放在里面的那个）")

    path = Path(folder)
    if not path.is_dir():
        raise HTTPException(status_code=422, detail=f"这个文件夹打不开：{folder}")

    result = export_audit_service.audit_folder(db, path)

    if payload.save or payload.folder:
        app_settings_service.update_settings(
            db,
            {
                "audit_folder": str(path),
                "audit_last_run": datetime.now().isoformat(timespec="seconds"),
            },
        )
    else:
        app_settings_service.update_settings(
            db, {"audit_last_run": datetime.now().isoformat(timespec="seconds")}
        )
    return result.to_dict()


@audit_router.get("/fs/dirs")
def browse_dirs(path: str | None = Query(None, description="留空 = 列出盘符")):
    """目录浏览（**只列目录**，不返回任何文件内容）。

    为什么要它：文件夹得让用户"看得见磁盘"地选（浏览器出于安全拿不到绝对路径），
    所以由后端提供这一层只读浏览。
    """
    if not path:
        drives = []
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            p = f"{ch}:\\"
            if os.path.isdir(p):
                drives.append({"name": p, "path": p})
        return {"path": None, "parent": None, "dirs": drives, "is_drive_list": True}

    target = Path(path)
    if not target.is_dir():
        raise HTTPException(status_code=422, detail=f"不是文件夹或打不开：{path}")

    dirs: list[dict] = []
    try:
        with os.scandir(target) as it:
            for entry in it:
                if len(dirs) >= _MAX_DIRS:
                    break
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if entry.name.startswith(".") or entry.name.startswith("$"):
                    continue
                dirs.append({"name": entry.name, "path": str(Path(entry.path))})
    except PermissionError as e:
        raise HTTPException(status_code=422, detail=f"没有权限读这个文件夹：{path}") from e

    dirs.sort(key=lambda d: d["name"])
    parent = str(target.parent) if target.parent != target else None
    return {"path": str(target), "parent": parent, "dirs": dirs, "is_drive_list": False}
