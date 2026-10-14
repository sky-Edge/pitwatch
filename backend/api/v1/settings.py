# -*- coding: utf-8 -*-
"""全站设置 API —— 主界面「项目默认值」用。

- GET /settings   读取（缺的键用默认值补齐）
- PUT /settings   修改（只改传进来的键）

用户 2026-10-11：「能不能把一些操作集中主界面上？每个项目都要调单独设置有点繁琐」
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.schemas.app_setting import AppSettingsRead, AppSettingsUpdate
from backend.services import app_settings_service

settings_router = APIRouter(prefix="/settings", tags=["settings"])


@settings_router.get("", response_model=AppSettingsRead)
def read_settings(db: Session = Depends(get_db)):
    """全站设置。"""
    return AppSettingsRead(**app_settings_service.get_settings(db))


@settings_router.put("", response_model=AppSettingsRead)
def write_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db)):
    """改设置。只改传进来的键 —— 没传的保持原样。

    ⚠ 这里**只影响以后新建的项目**，不会去动已经存在的项目（改历史配置是危险动作，
    要改请用项目列表里的「批量套用规则/母版」，那条路会明确告诉你影响了哪几个项目）。
    """
    data = payload.model_dump(exclude_unset=True)
    return AppSettingsRead(**app_settings_service.update_settings(db, data))
