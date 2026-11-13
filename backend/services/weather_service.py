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
import json
import threading
import time
from pathlib import Path
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

#: 备用源：wttr.in。2026-11-13 实测**这台服务器到 Open-Meteo 完全不通**
#: （连续三次 10 秒超时，exit 28），而 wttr.in 1.5 秒返回、坐标能定位到嘉善。
#: 代价：只给今天 + 2 天预报，**查不了历史** —— 补录旧日报仍得靠 Open-Meteo。
_WTTR_URL = "https://wttr.in/{lat},{lon}?format=j1"

#: wttr.in 用的是 WWO 天气码（113/176/296…），和 WMO 不是一套，必须单独映射。
_WWO_CN: dict[int, str] = {
    113: "晴",
    116: "多云",
    119: "多云",
    122: "阴",
    143: "雾",
    176: "阵雨",
    179: "阵雪",
    182: "雨夹雪",
    185: "冻雨",
    200: "雷阵雨",
    227: "雪",
    230: "大雪",
    248: "雾",
    260: "雾",
    263: "毛毛雨",
    266: "毛毛雨",
    281: "冻雨",
    284: "冻雨",
    293: "小雨",
    296: "小雨",
    299: "中雨",
    302: "中雨",
    305: "大雨",
    308: "大雨",
    311: "冻雨",
    314: "冻雨",
    317: "雨夹雪",
    320: "雨夹雪",
    323: "小雪",
    326: "小雪",
    329: "中雪",
    332: "中雪",
    335: "大雪",
    338: "大雪",
    350: "冰粒",
    353: "阵雨",
    356: "阵雨",
    359: "暴雨",
    362: "雨夹雪",
    365: "雨夹雪",
    368: "阵雪",
    371: "阵雪",
    374: "冰粒",
    377: "冰粒",
    386: "雷阵雨",
    389: "雷阵雨",
    392: "雷阵雪",
    395: "雷阵雪",
}

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

#: 内存缓存：同一天同一地点反复导出只查一次（日报批量导出会连着查很多次）
#: 值可以是 None —— 表示"查过了、没查到"，同样要缓存，否则网络不通时每次导出都干等。
_cache: dict[tuple[str, float, float], str | None] = {}
_cache_lock = threading.Lock()

#: 磁盘缓存的 TTL。历史天气基本不会变，今天的会变，失败的要早点重试。
_TTL_OK_PAST = 30 * 86400
_TTL_OK_TODAY = 6 * 3600
_TTL_FAIL = 30 * 60

_MISS = object()
_disk: dict[str, dict] | None = None
_disk_lock = threading.Lock()


def _disk_path() -> Path:
    """缓存文件位置。

    放在 `holiday_cache_dir` 的上级目录（即 cache/），**跟随既有环境变量隔离** ——
    铁律①：任何会写盘的目录都必须能被测试指向临时目录，不能碰生产。
    """
    from backend.core.config import settings

    return settings.holiday_cache_dir.parent / "weather.json"


def _disk_load() -> dict:
    global _disk
    with _disk_lock:
        if _disk is None:
            try:
                p = _disk_path()
                _disk = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            except Exception:  # noqa: BLE001 —— 缓存坏了当没有，不能挡导出
                _disk = {}
        return _disk


def _disk_read(key: str) -> Any:
    item = _disk_load().get(key)
    if not item:
        return _MISS
    day = key.split("|", 1)[0]
    if item.get("w"):
        # 历史日期查到的天气不会再变；当天/未来的可能变，只放 6 小时
        ttl = _TTL_OK_TODAY if day >= dt.date.today().isoformat() else _TTL_OK_PAST
    else:
        ttl = _TTL_FAIL  # 查失败的 30 分钟后再试，别每次导出都等超时
    if time.time() - float(item.get("t") or 0) > ttl:
        return _MISS
    return item.get("w")


def _disk_write(key: str, value: str | None) -> None:
    global _disk
    data = _disk_load()
    data[key] = {"w": value, "t": time.time()}
    try:
        p = _disk_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass  # 写不进去只影响下次要重查，不影响本次导出


def _remember(key: tuple[str, float, float], value: str | None) -> None:
    with _cache_lock:
        _cache[key] = value
    _disk_write("|".join(str(x) for x in key), value)


def _key_of(day: dt.date, latitude: float, longitude: float) -> tuple[str, float, float]:
    return (day.isoformat(), round(float(latitude), 4), round(float(longitude), 4))


#: 源级冷却：某个天气源刚失败过，就别再为它白等一次超时。
#: 按日期缓存只能挡"同一天"，挡不住"另一天的历史查询又去撞同一堵墙"。
_SOURCE_COOLDOWN = 30 * 60
_down: dict[str, float] = {}


def _is_down(name: str) -> bool:
    return time.time() - _down.get(name, 0) < _SOURCE_COOLDOWN


def _mark_down(name: str) -> None:
    _down[name] = time.time()


def _mark_up(name: str) -> None:
    _down.pop(name, None)


def wwo_to_cn(code: Any) -> str | None:
    try:
        return _WWO_CN.get(int(code))
    except (TypeError, ValueError):
        return None


def _wttr_on(day: dt.date, latitude: float, longitude: float, *, timeout: float = 3.0) -> str | None:
    """从 wttr.in 取某天天气（只覆盖今天 + 2 天预报）。"""
    delta = (dt.date.today() - day).days
    if delta < 0 or delta > 2 or _is_down("wttr"):
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(_WTTR_URL.format(lat=latitude, lon=longitude))
            r.raise_for_status()
            data = r.json()
    except Exception:  # noqa: BLE001
        _mark_down("wttr")
        return None
    _mark_up("wttr")

    for item in data.get("weather") or []:
        if item.get("date") != day.isoformat():
            continue
        hourly = item.get("hourly") or []
        if not hourly:
            return None
        # 取中午 12 点那一档，比凌晨更能代表全天的天气
        pick = hourly[4] if len(hourly) > 4 else hourly[0]
        return wwo_to_cn(pick.get("weatherCode"))
    return None


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


def weather_on(day: dt.date, latitude: float, longitude: float, *, timeout: float = 3.0) -> str | None:
    """查某地某天的天气（中文）。查不到返回 None —— 调用方要能容忍。

    ⚠ 超时 3 秒（不是 8 秒）：这是在**导出日报的主路径**上同步等着的。
    2026-11-13 实测悉尼节点到 Open-Meteo 要 8 秒（直连甚至 300 秒超时），
    导出从 0.45 秒变成 8.7 秒 —— 用户会以为系统卡死。
    等 3 秒拿不到就认输，回退「晴」，别让一个天气字段拖垮导出。
    """
    key = _key_of(day, latitude, longitude)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    cached = _disk_read("|".join(str(x) for x in key))
    if cached is not _MISS:
        with _cache_lock:
            _cache[key] = cached
        return cached

    today = dt.date.today()
    delta = (today - day).days

    # 近期日期（今天 + 2 天）先试 wttr.in：这台机器上它 1.5 秒能回，
    # open-meteo 却完全不通。查历史（delta > 2）才只能依赖 open-meteo。
    if 0 <= delta <= 2:
        cn = _wttr_on(day, latitude, longitude, timeout=min(timeout, 3.0))
        if cn:
            _remember(key, cn)
            return cn

    if _is_down("open-meteo"):
        return None

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
        # 失败也记下来（TTL 30 分钟）：网络不通时不能每次导出都干等 3 秒
        _mark_down("open-meteo")
        _remember(key, None)
        return None
    _mark_up("open-meteo")

    daily = data.get("daily") or {}
    times = daily.get("time") or []
    codes = daily.get("weathercode") or []
    if day.isoformat() in times:
        idx = times.index(day.isoformat())
        if idx < len(codes):
            cn = wmo_to_cn(codes[idx])
            if cn:
                _remember(key, cn)
                return cn
    _remember(key, None)
    return None


def weather_many(
    days: list[dt.date], latitude: float, longitude: float, *, timeout: float = 3.0
) -> dict[str, str]:
    """**一次**查多天（批量打印 N 期日报时用）。

    逐天调 `weather_on` 会发 N 个请求，每期都等一次超时 —— 批量打印 20 期就是
    20 次往返，用户点完要等一分钟。这里把日期按接口分成两段，
    每段只发一个请求，再按返回的时间序列回填缓存。
    """
    if not days:
        return {}

    wanted = sorted(set(days))
    today = dt.date.today()
    want_set = set(wanted)
    recent = [d for d in wanted if 0 <= (today - d).days <= 7]
    older = [d for d in wanted if (today - d).days > 7]

    def _pull(url: str, params: dict, expect: set[dt.date]) -> None:
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception:  # noqa: BLE001 —— 查不到就靠调用方回退，绝不往上抛
            return
        daily = data.get("daily") or {}
        times = daily.get("time") or []
        codes = daily.get("weathercode") or []
        for i, text in enumerate(times):
            if i >= len(codes):
                break
            try:
                day = dt.date.fromisoformat(text)
            except ValueError:
                continue
            if day in want_set and day in expect:
                _remember(_key_of(day, latitude, longitude), wmo_to_cn(codes[i]))

    # 近期日期先试 wttr（1 个请求覆盖 3 天，且这台机器上它是通的）
    wttr_days = [d for d in recent if (today - d).days <= 2]
    if wttr_days and not _is_down("wttr"):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(_WTTR_URL.format(lat=latitude, lon=longitude))
                r.raise_for_status()
                wdata = r.json()
            _mark_up("wttr")
            for item in wdata.get("weather") or []:
                try:
                    day = dt.date.fromisoformat(item.get("date") or "")
                except ValueError:
                    continue
                if day not in set(wttr_days):
                    continue
                hourly = item.get("hourly") or []
                if hourly:
                    pick = hourly[4] if len(hourly) > 4 else hourly[0]
                    cn = wwo_to_cn(pick.get("weatherCode"))
                    if cn:
                        _remember(_key_of(day, latitude, longitude), cn)
        except Exception:  # noqa: BLE001
            _mark_down("wttr")

    if recent and not _is_down("open-meteo"):
        back = max((today - d).days for d in recent)
        _pull(
            _FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "daily": "weathercode",
                "timezone": "Asia/Shanghai",
                "past_days": max(1, back),
                "forecast_days": 1,
            },
            set(recent),
        )
    if older:
        _pull(
            _ARCHIVE_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "start_date": older[0].isoformat(),
                "end_date": older[-1].isoformat(),
                "daily": "weathercode",
                "timezone": "Asia/Shanghai",
            },
            set(older),
        )

    out: dict[str, str] = {}
    for day in wanted:
        with _cache_lock:
            value = _cache.get(_key_of(day, latitude, longitude))
        if value:
            out[day.isoformat()] = value
    return out


def clear_cache(*, disk: bool = False) -> None:
    """测试用：清掉缓存（同一天改了位置时也能立即生效）。"""
    global _disk
    with _cache_lock:
        _cache.clear()
    if disk:
        with _disk_lock:
            _disk = {}
        try:
            _disk_path().unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
