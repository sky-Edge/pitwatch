# -*- coding: utf-8 -*-
"""导出日报时自动填天气。

用户 2026-10-29：「导出的天气要自己修很麻烦，可以用网络访问本地天气吗」。

三条判据：
  ① 没配位置 → 行为跟以前一样（「晴」），不能因为没配就报错；
  ② 配了位置 → 自动按那天查，查不到**必须回退**（不能因为天气接口挂了就导不出日报）；
  ③ 手选的天气永远优先于自动 —— 用户明确写了就要听用户的。
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.api.v1.reports import _resolve_weather
from backend.services import app_settings_service, weather_service


def _set_location(client, label: str = "嘉善") -> None:
    r = client.put(
        "/api/v1/settings",
        json={"weather_location": {"label": label, "latitude": 30.85, "longitude": 120.93}},
    )
    assert r.status_code == 200, r.text


def test_preview_without_location(client) -> None:
    r = client.get("/api/v1/weather/preview")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_preview_with_location(client, monkeypatch) -> None:
    _set_location(client)
    monkeypatch.setattr(weather_service, "weather_on", lambda d, lat, lon, **k: "小雨")
    r = client.get("/api/v1/weather/preview", params={"obs_date": "2026-10-20"})
    body = r.json()
    assert body["configured"] is True
    assert body["weather"] == "小雨"
    assert body["label"] == "嘉善"


def test_manual_weather_wins(client, monkeypatch) -> None:
    """手选 > 自动。用户明确写了「大雨」，不能被接口查出来的「晴」覆盖。"""
    _set_location(client)
    monkeypatch.setattr(weather_service, "weather_on", lambda d, lat, lon, **k: "晴")

    from backend.core.db import SessionLocal

    with SessionLocal() as db:
        assert _resolve_weather(db, "大雨", date(2026, 10, 20)) == "大雨"


def test_auto_when_no_manual(client, monkeypatch) -> None:
    _set_location(client)
    monkeypatch.setattr(weather_service, "weather_on", lambda d, lat, lon, **k: "多云")

    from backend.core.db import SessionLocal

    with SessionLocal() as db:
        assert _resolve_weather(db, None, date(2026, 10, 20)) == "多云"
        assert _resolve_weather(db, "", date(2026, 10, 20)) == "多云"


def test_fallback_to_sunny_when_lookup_fails(client, monkeypatch) -> None:
    """天气接口挂了也必须能导出日报 —— 回退「晴」，跟没配之前一样。"""
    _set_location(client)
    monkeypatch.setattr(weather_service, "weather_on", lambda d, lat, lon, **k: None)

    from backend.core.db import SessionLocal

    with SessionLocal() as db:
        assert _resolve_weather(db, None, date(2026, 10, 20)) == "晴"


def test_fallback_when_service_raises(client, monkeypatch) -> None:
    """接口抛异常同样不能连累导出。"""
    _set_location(client)

    def boom(*a, **k):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr(weather_service, "weather_on", boom)

    from backend.core.db import SessionLocal

    with SessionLocal() as db:
        assert _resolve_weather(db, None, date(2026, 10, 20)) == "晴"


def test_wmo_mapping() -> None:
    """WMO 数字要翻成日报里习惯写的中文。"""
    assert weather_service.wmo_to_cn(0) == "晴"
    assert weather_service.wmo_to_cn(3) == "阴"
    assert weather_service.wmo_to_cn(61) == "小雨"
    assert weather_service.wmo_to_cn(95) == "雷阵雨"
    assert weather_service.wmo_to_cn(9999) is None
    assert weather_service.wmo_to_cn(None) is None
