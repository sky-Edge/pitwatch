# -*- coding: utf-8 -*-
"""天气：按位置自动填日报里的「天气」栏。

用户 2026-10-29：「导出的天气要自己修很麻烦，可以用网络访问本地天气吗，
或者用户自己选地址，你来获取当天天气」。

【为什么用 Open-Meteo】
· 免费、**不需要 API Key**（不用注册、不会有额度突然到期的问题）
· 有历史存档（`archive-api`）—— 日报经常是补录前几天的，必须能查过去
· 城市搜索带 `language=zh`，返回中文名

【三件事容易做错】
1. **日期分叉**：近 7 天走 `forecast`（`past_days` 往回带），更早走 `archive`。
   只用一个接口会出现"补录上周的日报查不到天气"。
2. **天气码是 WMO 数字**（0/2/61/95…），必须翻成中文再写进日报。
3. **不能因为天气查不到就不让导出** —— 网络抖一下就导不出日报，是本末倒置。
   查不到返回 None，调用方回退到用户手选或"晴"。
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import httpx

#: WMO 天气码 → 日报里习惯写的中文。颗粒度按日报需要，不照搬气象术语。
WMO_CN: dict[int, str] = {
    0: "晴",
    1: "晴间多云",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨",
    56: "冻雨",
    57: "冻雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪",
    80: "阵雨",
    81: "阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "阵雪",
    95: "雷阵雨",
    96: "雷阵雨",
    99: "雷阵雨",
}

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

#: 简单内存缓存：同一天同一地点反复导出只查一次（日报批量导出会连着查很多次）
_cache: dict[tuple[str, float, float], str] = {}
_cache_lock = threading.Lock()


def wmo_to_cn(code: Any) -> str | None:
    try:
        return WMO_CN.get(int(code))
    except (TypeError, ValueError):
        return None


def geocode(name: str, *, count: int = 5, timeout: float = 8.0) -> list[dict]:
    """城市名 → 候选坐标。支持中文/拼音（Open-Meteo 对纯中文支持差，拼音更稳）。"""
    q = (name or "").strip()
    if not q:
        return []
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(_GEOCODE_URL, params={"name": q, "count": count, "language": "zh"})
            r.raise_for_status()
            data = r.json()
    except Exception:  # noqa: BLE001 —— 查不到就是空列表，不往上抛
        return []

    out = []
    for item in data.get("results") or []:
        out.append(
            {
                "label": item.get("name") or q,
                "admin": item.get("admin1") or "",
                "country": item.get("country") or "",
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
            }
        )
    return out


def weather_on(day: dt.date, latitude: float, longitude: float, *, timeout: float = 8.0) -> str | None:
    """查某地某天的天气（中文）。查不到返回 None —— 调用方要能容忍。"""
    key = (day.isoformat(), round(float(latitude), 4), round(float(longitude), 4))
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    today = dt.date.today()
    delta = (today - day).days

    try:
        with httpx.Client(timeout=timeout) as client:
            if 0 <= delta <= 7:
                # 近几天：forecast 接口往回带 7 天
                r = client.get(
                    _FORECAST_URL,
                    params={
                        "latitude": latitude,
                        "longitude": longitude,
                        "daily": "weathercode",
                        "timezone": "Asia/Shanghai",
                        "past_days": max(1, delta),
                        "forecast_days": 1,
                    },
                )
            else:
                # 更早（补录日报）：走历史存档
                r = client.get(
                    _ARCHIVE_URL,
                    params={
                        "latitude": latitude,
                        "longitude": longitude,
                        "start_date": day.isoformat(),
                        "end_date": day.isoformat(),
                        "daily": "weathercode",
                        "timezone": "Asia/Shanghai",
                    },
                )
            r.raise_for_status()
            data = r.json()
    except Exception:  # noqa: BLE001
        return None

    daily = data.get("daily") or {}
    times = daily.get("time") or []
    codes = daily.get("weathercode") or []
    if day.isoformat() in times:
        idx = times.index(day.isoformat())
        if idx < len(codes):
            cn = wmo_to_cn(codes[idx])
            if cn:
                with _cache_lock:
                    _cache[key] = cn
                return cn
    return None


def clear_cache() -> None:
    """测试用：清掉缓存（同一天改了位置时也能立即生效）。"""
    with _cache_lock:
        _cache.clear()
