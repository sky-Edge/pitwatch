# -*- coding: utf-8 -*-
"""天气 API（导出日报时自动填天气用）。

- GET /weather/geocode?name=Jiashan   城市名 → 候选坐标（设置里选位置用）
- GET /weather/preview?date=2026-10-29 按已配置位置查某天天气（导出页预览用）
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.services import app_settings_service, weather_service

weather_router = APIRouter(prefix="/weather", tags=["weather"])


@weather_router.get("/geocode")
def geocode(name: str = Query(..., min_length=1, max_length=40)):
    """城市名找坐标。中文支持一般，**拼音更稳**（Jiashan），提示里写清楚。"""
    results = weather_service.geocode(name)
    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"没找到「{name}」。中文地名可能搜不到，试试拼音（如 Jiashan、Jiaxing、Shanghai）",
        )
    return results


@weather_router.get("/preview")
def preview(
    obs_date: date | None = Query(None, description="要查哪天的天气，默认今天"),
    db: Session = Depends(get_db),
):
    """按设置里的位置查某天天气（给导出页显示「今天自动填什么」）。"""
    loc = (app_settings_service.get_settings(db).get("weather_location") or {})
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        return {"configured": False, "weather": None, "label": None}

    day = obs_date or date.today()
    text = weather_service.weather_on(day, float(lat), float(lon))
    return {
        "configured": True,
        "label": loc.get("label"),
        "date": day.isoformat(),
        "weather": text,
    }
