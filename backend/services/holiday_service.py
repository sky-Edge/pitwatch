# -*- coding: utf-8 -*-
"""节假日安排：联网更新 + 本地缓存 + 内置保底。

【为什么需要它】
日期选择面板要标出「哪天放假、哪天调休上班」——现场排观测计划时，遇到放假要提前，
遇到"调休上班的周六"（那天其实要上工）也得当成工作日。但放假安排**每年由上一年 11 月
国务院办公厅才公布**，写死在代码里就意味着每年都要改一次代码、重新发一版。

【三层取值顺序】
1. **本地缓存**（`settings.holiday_cache_dir/holidays_<year>.json`）且未过期 → 直接用；
2. **联网拉取**（timor.tech，实测本机可直连）→ 成功则写缓存；
3. 都拿不到 → **内置的 2026 年数据**（照国务院办公厅《关于 2026 年部分节假日安排的通知》
   2025-11-04 抄录）或空表。

【绝不因为网络问题影响功能】所有网络异常都被吞掉并降级，接口始终返回 200。
用户可能在地下车库、工地现场没有网 —— 那时候"看不到节假日标记"是小事，
"整个页面报错"才是大事。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from backend.core.config import settings

# 数据源（按顺序尝试）。{year} 会被替换成四位年份。
# 实测（2026-09-28，本机）：timor.tech 0.7s 返回；jsDelivr / GitHub raw 均被网络阻断，
# 所以只保留这一个可达源，但保留"多源依次尝试"的结构以便将来补源。
DATA_SOURCES: tuple[str, ...] = ("https://timor.tech/api/holiday/year/{year}",)

# 联网请求超时（秒）。宁可拿不到，也不能让页面卡住 —— 它是在页面渲染路径上被请求的。
TIMEOUT_SEC = 5

# 缓存有效期：有数据的 30 天；空结果 3 天（某年安排还没公布时别把"空"缓存一整月）
CACHE_TTL_DAYS = 30
EMPTY_CACHE_TTL_DAYS = 3

USER_AGENT = "Mozilla/5.0 (compatible; pit-monitor/1.0)"


# ────────────────────────── 内置数据（离线保底） ──────────────────────────
#
# 与 `frontend/src/calendar/holidays.ts` 的 2026 数据必须一致 —— 有测试比对
# （tests/test_holidays.py::test_frontend_builtin_matches_backend），
# 两边不一致时宁可测试失败，也不要让"联网与否"给出两种答案。

BUILTIN: dict[int, dict[str, list]] = {
    2026: {
        # (起始, 结束, 节日名) —— 与通知逐条对应，共 33 天
        "rest_ranges": [
            ("2026-01-01", "2026-01-03", "元旦"),
            ("2026-02-15", "2026-02-23", "春节"),
            ("2026-04-04", "2026-04-06", "清明节"),
            ("2026-05-01", "2026-05-05", "劳动节"),
            ("2026-06-19", "2026-06-21", "端午节"),
            ("2026-09-25", "2026-09-27", "中秋节"),
            ("2026-10-01", "2026-10-07", "国庆节"),
        ],
        # (日期, 说明) —— 共 6 天
        "makeup": [
            ("2026-01-04", "元旦调休上班"),
            ("2026-02-14", "春节调休上班"),
            ("2026-02-28", "春节调休上班"),
            ("2026-05-09", "劳动节调休上班"),
            ("2026-09-20", "中秋节调休上班"),
            ("2026-10-10", "国庆节调休上班"),
        ],
    },
}


@dataclass
class HolidayTable:
    """某一年的节假日安排。"""

    year: int
    # 放假日：{'YYYY-MM-DD': 节日名}
    rest: dict[str, str] = field(default_factory=dict)
    # 调休上班日：{'YYYY-MM-DD': 说明}
    makeup: dict[str, str] = field(default_factory=dict)
    # 数据来源：network（联网）/ cache（本地缓存）/ builtin（内置）/ none（没有）
    source: str = "none"
    fetched_at: str | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.rest or self.makeup)

    def to_dict(self) -> dict:
        return {
            "year": self.year,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "has_data": self.has_data,
            "rest": [{"date": d, "name": n} for d, n in sorted(self.rest.items())],
            "makeup": [{"date": d, "name": n} for d, n in sorted(self.makeup.items())],
        }


# ────────────────────────── 工具 ──────────────────────────


def _expand(frm: str, to: str) -> list[str]:
    """把区间展开成逐日列表（含首尾）。"""
    d0, d1 = date.fromisoformat(frm), date.fromisoformat(to)
    if d1 < d0:  # 写反了宁可返回空，也不要死循环
        return []
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _cache_path(year: int):
    return settings.holiday_cache_dir / f"holidays_{year}.json"


def _builtin_table(year: int) -> HolidayTable | None:
    raw = BUILTIN.get(year)
    if not raw:
        return None
    rest: dict[str, str] = {}
    for frm, to, name in raw["rest_ranges"]:
        for d in _expand(frm, to):
            rest[d] = name
    makeup = {d: n for d, n in raw["makeup"]}
    return HolidayTable(year=year, rest=rest, makeup=makeup, source="builtin", fetched_at=None)


# ────────────────────────── 解析 ──────────────────────────


def parse_timor(payload: dict, year: int) -> HolidayTable | None:
    """把 timor.tech 的返回标准化成 HolidayTable。

    返回结构（实测 2026）::

        {"code": 0, "holiday": {
            "01-01": {"holiday": true,  "name": "元旦",   "date": "2026-01-01"},
            "01-04": {"holiday": false, "name": "元旦后补班", "date": "2026-01-04", "target": "元旦"},
        }}

    `holiday=true` 是放假；`false` 是调休上班（补班）。
    """
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        return None
    items = payload.get("holiday")
    if not isinstance(items, dict):
        return None

    rest: dict[str, str] = {}
    makeup: dict[str, str] = {}
    for v in items.values():
        if not isinstance(v, dict):
            continue
        d = v.get("date")
        name = (v.get("name") or "").strip()
        if not d or not isinstance(d, str) or len(d) != 10:
            continue
        # 只收该年的（接口偶尔会带上跨年补班日）
        if not d.startswith(f"{year}-"):
            continue
        if v.get("holiday"):
            rest[d] = name or "放假"
        else:
            makeup[d] = name or "调休上班"
    # 注意：**空结果也要返回 table**（而不是 None）。
    # 返回 None 会让上层以为"网络失败"，于是每次翻到这一年都重新联网 ——
    # 而实际情况是"这一年还没公布"，缓存 3 天即可（见 EMPTY_CACHE_TTL_DAYS）。
    return HolidayTable(year=year, rest=rest, makeup=makeup, source="network",
                        fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# ────────────────────────── 缓存 ──────────────────────────


def _read_cache(year: int) -> HolidayTable | None:
    p = _cache_path(year)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    ttl = CACHE_TTL_DAYS if (raw.get("rest") or raw.get("makeup")) else EMPTY_CACHE_TTL_DAYS
    ts = raw.get("cached_at")
    if ts:
        try:
            if datetime.now() - datetime.fromisoformat(ts) > timedelta(days=ttl):
                return None  # 过期，交给上层去联网
        except Exception:
            pass
    return HolidayTable(
        year=year,
        rest={x["date"]: x["name"] for x in raw.get("rest", [])},
        makeup={x["date"]: x["name"] for x in raw.get("makeup", [])},
        source="cache",
        fetched_at=raw.get("fetched_at"),
    )


def _write_cache(table: HolidayTable) -> None:
    """写缓存。写盘失败不应影响功能（磁盘满/只读目录时静默跳过）。"""
    try:
        settings.holiday_cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "year": table.year,
            "cached_at": datetime.now().isoformat(timespec="seconds"),
            "fetched_at": table.fetched_at,
            "rest": [{"date": d, "name": n} for d, n in sorted(table.rest.items())],
            "makeup": [{"date": d, "name": n} for d, n in sorted(table.makeup.items())],
        }
        _cache_path(table.year).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:
        pass


# ────────────────────────── 联网 ──────────────────────────


def fetch_from_network(year: int, timeout: float = TIMEOUT_SEC) -> HolidayTable | None:
    """依次尝试数据源，成功即返回。全部失败返回 None（**不抛异常**）。"""
    # 显式不带代理：项目机器上存在 HTTP(S)_PROXY 时也要走直连（与探针同一处理）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for tpl in DATA_SOURCES:
        url = tpl.format(year=year)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            continue
        table = parse_timor(payload, year)
        if table:
            return table
    return None


# ────────────────────────── 对外入口 ──────────────────────────


def get_holidays(year: int, force_network: bool = False) -> HolidayTable:
    """取某年的节假日安排。任何情况下都返回一个 HolidayTable（可能 has_data=False）。"""
    if not force_network:
        # `_read_cache` 只在"有缓存且未过期"时返回；有效期已按有无数据分别设定
        # （有数据 30 天 / 空结果 3 天），所以拿到就直接用 —— 包括"这一年还没公布"的空表，
        # 否则每翻到这一年都要联网等一次。
        cached = _read_cache(year)
        if cached is not None:
            if cached.has_data:
                return cached
            # 缓存里是空表：优先给内置（同样是空就直接返回空表）
            return _builtin_table(year) or cached

    if not is_offline_env():
        fetched = fetch_from_network(year)
        if fetched is not None:
            _write_cache(fetched)
            if fetched.has_data:
                return fetched
            # 网络通了，但这一年还没有安排（通常因为官方还没公布）
            return _builtin_table(year) or fetched

    # 联网失败 / 明确离线：过期缓存 → 内置 → 空表
    cached = _read_cache_ignoring_ttl(year)
    if cached is not None and cached.has_data:
        return cached
    builtin = _builtin_table(year)
    if builtin is not None:
        return builtin
    return HolidayTable(year=year, source="none")


def _read_cache_ignoring_ttl(year: int) -> HolidayTable | None:
    """联网失败时的兜底：过期缓存也比没有强。"""
    p = _cache_path(year)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return HolidayTable(
        year=year,
        rest={x["date"]: x["name"] for x in raw.get("rest", [])},
        makeup={x["date"]: x["name"] for x in raw.get("makeup", [])},
        source="cache",
        fetched_at=raw.get("fetched_at"),
    )


def builtin_years() -> list[int]:
    """内置了哪几年的数据（供测试与界面提示）。"""
    return sorted(BUILTIN.keys())


def is_offline_env() -> bool:
    """是否明确要求离线（设了 MONITOR_OFFLINE=1 时完全不联网）。

    给"现场完全没网、连超时都不想要"的场景留的后门 ——
    默认不开，因为 5 秒超时已经足够短。
    """
    return os.environ.get("MONITOR_OFFLINE", "").strip() in ("1", "true", "yes")
