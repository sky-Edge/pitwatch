# -*- coding: utf-8 -*-
"""导出日报时自动填天气。

用户 2026-10-29：「导出的天气要自己修很麻烦，可以用网络访问本地天气吗」。

三条判据：
  ① 没配位置 → 行为跟以前一样（「晴」），不能因为没配就报错；
  ② 配了位置 → 自动按那天查，查不到**必须回退**（不能因为天气接口挂了就导不出日报）；
  ③ 手选的天气永远优先于自动 —— 用户明确写了就要听用户的。
"""
from __future__ import annotations

import datetime as dt
import time

import pytest
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


# ───────────── 缓存：导出不能被天气拖慢（2026-11-13 实测 8.7s） ─────────────


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_httpx(monkeypatch, payload, calls, *, boom: bool = False):
    """把 httpx.Client 换掉，记录请求次数。"""

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            calls.append((url, params))
            if boom:
                raise RuntimeError("network down")
            return _FakeResp(payload)

    monkeypatch.setattr(weather_service.httpx, "Client", FakeClient)


def _reset_cache():
    weather_service.clear_cache(disk=True)
    weather_service._disk = None


def test_failed_lookup_is_cached(monkeypatch) -> None:
    """查失败也要记下来 —— 否则网络不通时每次导出都干等一次超时。"""
    _reset_cache()
    calls = []
    _fake_httpx(monkeypatch, None, calls, boom=True)
    today = dt.date.today()

    assert weather_service.weather_on(today, 30.85, 120.93) is None
    assert weather_service.weather_on(today, 30.85, 120.93) is None
    assert len(calls) == 1, "失败结果没缓存，导出会每次等 3 秒"


def test_disk_cache_survives_restart(monkeypatch) -> None:
    """容器重启后不该重新查 —— 内存清了，磁盘还在。"""
    _reset_cache()
    calls = []
    today = dt.date.today()
    payload = {"daily": {"time": [today.isoformat()], "weathercode": [61]}}
    _fake_httpx(monkeypatch, payload, calls)

    assert weather_service.weather_on(today, 30.85, 120.93) == "小雨"
    assert len(calls) == 1

    # 模拟进程重启：内存清空、磁盘保留
    weather_service._cache.clear()
    weather_service._disk = None

    assert weather_service.weather_on(today, 30.85, 120.93) == "小雨"
    assert len(calls) == 1, "磁盘缓存没命中，重启后每次导出都要重查"


def test_weather_many_uses_one_request_for_range(monkeypatch) -> None:
    """批量打印 5 期只发 1 个请求，不是 5 个。"""
    _reset_cache()
    calls = []
    today = dt.date.today()
    days = [today - dt.timedelta(days=i) for i in range(5)]
    ordered = sorted(days)
    payload = {
        "daily": {
            "time": [d.isoformat() for d in ordered],
            "weathercode": [0, 1, 2, 3, 61],
        }
    }
    _fake_httpx(monkeypatch, payload, calls)

    got = weather_service.weather_many(days, 30.85, 120.93)
    assert len(calls) == 1, f"应该是 1 次请求，实际 {len(calls)} 次"
    assert len(got) == 5
    assert got[ordered[0].isoformat()] == "晴"
    assert got[ordered[-1].isoformat()] == "小雨"


def test_stale_disk_entry_is_ignored(monkeypatch) -> None:
    """超过 TTL 的缓存要重查（历史 30 天、当天 6 小时、失败 30 分钟）。"""
    _reset_cache()
    calls = []
    day = dt.date.today() - dt.timedelta(days=40)
    weather_service._disk = {
        f"{day.isoformat()}|30.85|120.93": {"w": "小雨", "t": time.time() - 40 * 86400}
    }
    payload = {"daily": {"time": [day.isoformat()], "weathercode": [3]}}
    _fake_httpx(monkeypatch, payload, calls)

    assert weather_service.weather_on(day, 30.85, 120.93) == "阴"
    assert len(calls) == 1, "过期缓存没有重查"


def test_timeout_is_short_because_it_blocks_export() -> None:
    """默认超时必须短：它在导出主路径上同步等着的。"""
    import inspect

    sig = inspect.signature(weather_service.weather_on)
    assert sig.parameters["timeout"].default <= 3.0
    assert inspect.signature(weather_service.weather_many).parameters["timeout"].default <= 3.0
