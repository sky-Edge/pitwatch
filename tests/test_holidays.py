# -*- coding: utf-8 -*-
"""节假日服务测试：内置数据、联网解析、缓存、降级。

为什么这几件事都要测：

1. **内置数据**：写错一天，界面上只会"少标一个休"，肉眼几乎发现不了；
   而这天可能是用户特意避开的。
2. **联网解析**：第三方返回的字段名/语义（`holiday: true/false` = 放假/补班）如果理解反了，
   会把"要上班的周六"标成"放假"—— 反向误导，比不标更糟。
3. **降级链**：现场经常没网。降级一旦出错就是 500，而它只是日历上的装饰信息。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.core.config import settings
from backend.services import holiday_service as hs

# 国务院办公厅《关于 2026 年部分节假日安排的通知》（2025-11-04）
EXPECTED_RANGES = {
    "元旦": ("2026-01-01", "2026-01-03"),
    "春节": ("2026-02-15", "2026-02-23"),
    "清明节": ("2026-04-04", "2026-04-06"),
    "劳动节": ("2026-05-01", "2026-05-05"),
    "端午节": ("2026-06-19", "2026-06-21"),
    "中秋节": ("2026-09-25", "2026-09-27"),
    "国庆节": ("2026-10-01", "2026-10-07"),
}
EXPECTED_TOTAL_DAYS = 33
EXPECTED_MAKEUP = {
    "2026-01-04",
    "2026-02-14",
    "2026-02-28",
    "2026-05-09",
    "2026-09-20",
    "2026-10-10",
}


def _clear_cache(year: int) -> None:
    p = hs._cache_path(year)
    if p.is_file():
        p.unlink()


def _online(monkeypatch) -> None:
    """临时打开联网开关。

    conftest 为了"测试不依赖外网"统一设了 `MONITOR_OFFLINE=1`；
    要验证联网/缓存路径的用例必须先把这道开关摘掉，否则 `fetch_from_network` 根本不会被调用
    （表现为"网络调用次数 0"这种看起来像产品 bug、实则是测试环境导致的现象）。
    """
    monkeypatch.delenv("MONITOR_OFFLINE", raising=False)


# ────────────────────────── 1. 内置数据与官方通知一致 ──────────────────────────


def test_builtin_2026_matches_official_notice() -> None:
    """内置 2026 数据必须与国务院通知逐条一致（它是没网时的唯一依据）。"""
    got = {name: (frm, to) for frm, to, name in hs.BUILTIN[2026]["rest_ranges"]}
    assert got == EXPECTED_RANGES, f"放假区间与通知不符：\n  实际 {got}\n  预期 {EXPECTED_RANGES}"

    total = 0
    for frm, to, _name in hs.BUILTIN[2026]["rest_ranges"]:
        days = hs._expand(frm, to)
        assert days, f"区间方向写反或为空：{frm} → {to}"
        total += len(days)
    assert total == EXPECTED_TOTAL_DAYS, f"放假日共 {total} 天，通知写的是 {EXPECTED_TOTAL_DAYS} 天"


def test_builtin_makeup_days_match_and_fall_on_weekend() -> None:
    """调休上班日必须与通知一致，且都落在周末（否则"调休"二字没有意义）。"""
    got = {d for d, _ in hs.BUILTIN[2026]["makeup"]}
    assert got == EXPECTED_MAKEUP, f"调休上班日不符：{sorted(got ^ EXPECTED_MAKEUP)}"
    for d in sorted(got):
        wd = dt.date.fromisoformat(d).weekday()  # 0=周一 … 5=周六 6=周日
        assert wd >= 5, f"{d} 是 weekday={wd}，不是周末，不该是调休上班日"


def test_builtin_rest_and_makeup_do_not_overlap() -> None:
    """同一天不可能既放假又调休上班。"""
    table = hs._builtin_table(2026)
    assert not (set(table.rest) & set(table.makeup)), "放假日与调休上班日重叠"


def test_builtin_table_source_is_builtin() -> None:
    t = hs._builtin_table(2026)
    assert t is not None and t.source == "builtin" and t.has_data


def test_builtin_table_none_for_unknown_year() -> None:
    assert hs._builtin_table(2099) is None


# ────────────────────────── 2. 联网返回的解析 ──────────────────────────


TIMOR_SAMPLE = {
    "code": 0,
    "holiday": {
        "01-01": {"holiday": True, "name": "元旦", "date": "2026-01-01"},
        "01-04": {"holiday": False, "name": "元旦后补班", "date": "2026-01-04", "target": "元旦"},
        # 跨年补班日：不该被收进来
        "12-31": {"holiday": False, "name": "去年补班", "date": "2025-12-31"},
    },
}


def test_parse_timor_splits_rest_and_makeup() -> None:
    """`holiday=true` 是放假、`false` 是补班 —— 理解反了会把上班日标成放假。"""
    t = hs.parse_timor(TIMOR_SAMPLE, 2026)
    assert t is not None
    assert t.year == 2026 and t.source == "network"
    assert list(t.rest) == ["2026-01-01"], f"放假解析错误：{t.rest}"
    assert list(t.makeup) == ["2026-01-04"], f"补班解析错误：{t.makeup}"
    assert t.makeup["2026-01-04"] == "元旦后补班"


def test_parse_timor_ignores_other_years() -> None:
    """接口偶尔会带上跨年补班日，只收目标年份。"""
    t = hs.parse_timor(TIMOR_SAMPLE, 2026)
    assert all(d.startswith("2026-") for d in list(t.rest) + list(t.makeup))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},                                       # 没有 code/holiday
        {"code": 1, "holiday": {}},               # 业务错误
        {"code": 0, "holiday": "oops"},           # 结构不对
    ],
)
def test_parse_timor_rejects_bad_payload(payload) -> None:
    """接口异常时应返回 None（让上层去降级），而不是抛异常。"""
    assert hs.parse_timor(payload, 2026) is None


def test_parse_timor_empty_year_returns_empty_table_not_none() -> None:
    """结构合法但该年没数据时返回**空表**而非 None。

    返回 None 会被上层当成"网络失败"，于是每次翻到这一年都重新联网；
    而真相是"这一年还没公布"，缓存 3 天即可。
    """
    t = hs.parse_timor({"code": 0, "holiday": {}}, 2027)
    assert t is not None and not t.has_data and t.source == "network"


# ────────────────────────── 3. 缓存与降级 ──────────────────────────


def test_cache_roundtrip(monkeypatch) -> None:
    """联网成功后写缓存；再取时直接命中缓存（不联网）。"""
    _online(monkeypatch)
    year = 2031
    _clear_cache(year)
    fake = hs.parse_timor(TIMOR_SAMPLE, year)  # 借这个结构造一份网络结果
    assert fake is not None
    fake.year = year
    fake.rest = {f"{year}-01-01": "元旦"}
    fake.makeup = {}

    monkeypatch.setattr(hs, "fetch_from_network", lambda y, timeout=5: fake)
    first = hs.get_holidays(year)
    assert first.source == "network" and hs._cache_path(year).is_file()

    # 断网后仍能拿到（走缓存）
    monkeypatch.setattr(hs, "fetch_from_network", lambda y, timeout=5: None)
    second = hs.get_holidays(year)
    assert second.source == "cache" and second.rest == {f"{year}-01-01": "元旦"}
    _clear_cache(year)


def test_network_failure_falls_back_to_builtin(monkeypatch) -> None:
    """联网失败时用内置数据兜底（现场没网也要能看到放假安排）。"""
    _online(monkeypatch)
    _clear_cache(2026)
    monkeypatch.setattr(hs, "fetch_from_network", lambda y, timeout=5: None)
    t = hs.get_holidays(2026)
    assert t.source == "builtin" and len(t.rest) == EXPECTED_TOTAL_DAYS


def test_unknown_year_without_network_returns_empty(monkeypatch) -> None:
    """既没网、也没内置（比如 2099 年）→ 返回空表而不是报错。"""
    _online(monkeypatch)
    _clear_cache(2099)
    monkeypatch.setattr(hs, "fetch_from_network", lambda y, timeout=5: None)
    t = hs.get_holidays(2099)
    assert not t.has_data and t.source == "none"
    assert t.to_dict()["rest"] == [] and t.to_dict()["makeup"] == []


def test_expired_cache_is_refetched(monkeypatch) -> None:
    """缓存过期（超 30 天）后应重新联网。"""
    _online(monkeypatch)
    year = 2032
    _clear_cache(year)
    hs._write_cache(
        hs.HolidayTable(year=year, rest={f"{year}-05-01": "劳动节"}, makeup={}, source="cache")
    )
    p = hs._cache_path(year)
    raw = json.loads(p.read_text(encoding="utf-8"))
    old = (dt.datetime.now() - dt.timedelta(days=hs.CACHE_TTL_DAYS + 1)).isoformat(timespec="seconds")
    raw["cached_at"] = old
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    called = {"n": 0}

    def fake_fetch(year_, timeout=5):
        called["n"] += 1
        return hs.HolidayTable(year=year_, rest={f"{year_}-10-01": "国庆节"}, source="network")

    monkeypatch.setattr(hs, "fetch_from_network", fake_fetch)
    t = hs.get_holidays(year)
    assert called["n"] == 1, "过期缓存没有被重新联网"
    assert t.source == "network"
    _clear_cache(year)


def test_empty_result_cached_with_short_ttl(monkeypatch) -> None:
    """空结果（该年还没公布）也要缓存 —— 否则每次翻到这一年都联网等一次。"""
    _online(monkeypatch)
    year = 2033
    _clear_cache(year)
    calls = {"n": 0}

    def fake_fetch(year_, timeout=5):
        calls["n"] += 1
        return hs.HolidayTable(year=year_, source="network")

    monkeypatch.setattr(hs, "fetch_from_network", fake_fetch)
    hs.get_holidays(year)
    hs.get_holidays(year)
    hs.get_holidays(year)
    assert calls["n"] == 1, f"空结果没有缓存，联网了 {calls['n']} 次"
    assert hs._cache_path(year).is_file()
    _clear_cache(year)


def test_offline_env_skips_network_entirely(monkeypatch) -> None:
    """MONITOR_OFFLINE=1 时完全不联网（连超时都不想要）。"""
    _clear_cache(2026)
    monkeypatch.setenv("MONITOR_OFFLINE", "1")
    monkeypatch.setattr(hs, "fetch_from_network", lambda y, timeout=5: pytest.fail("不该联网"))
    t = hs.get_holidays(2026)
    assert t.source == "builtin"


def test_write_cache_failure_is_silent(monkeypatch) -> None:
    """写缓存失败（磁盘满 / 只读目录）不能影响功能。"""
    monkeypatch.setattr(Path, "write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    hs._write_cache(hs.HolidayTable(year=2034, rest={"2034-01-01": "元旦"}, source="network"))


# ────────────────────────── 4. 接口 ──────────────────────────


def test_api_returns_builtin_when_offline(client) -> None:
    """测试环境是离线的（conftest 设了 MONITOR_OFFLINE）→ 接口应返回内置数据。"""
    _clear_cache(2026)
    r = client.get("/api/v1/holidays/2026")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year"] == 2026
    assert body["has_data"] is True
    assert body["source"] == "builtin"
    assert len(body["rest"]) == EXPECTED_TOTAL_DAYS
    assert {x["date"] for x in body["makeup"]} == EXPECTED_MAKEUP


def test_api_unknown_year_is_still_200(client) -> None:
    """拿不到数据也要 200 —— 前端靠 has_data 判断，不能因为装饰性信息报错。"""
    _clear_cache(2099)
    r = client.get("/api/v1/holidays/2099")
    assert r.status_code == 200
    assert r.json()["has_data"] is False


@pytest.mark.parametrize("year", [1999, 2101])
def test_api_rejects_out_of_range_year(client, year: int) -> None:
    """防手滑请求 /holidays/99999 去打第三方接口。"""
    assert client.get(f"/api/v1/holidays/{year}").status_code == 400


def test_refresh_endpoint_exists(client, monkeypatch) -> None:
    """强制刷新接口存在，且离线时也能优雅返回。"""
    _clear_cache(2026)
    monkeypatch.setattr(hs, "fetch_from_network", lambda y, timeout=5: None)
    r = client.post("/api/v1/holidays/2026/refresh")
    assert r.status_code == 200
    assert r.json()["source"] == "builtin"


# ────────────────────────── 5. 隔离（铁律） ──────────────────────────


def test_holiday_cache_dir_is_isolated_from_production() -> None:
    """节假日缓存目录必须被 conftest 隔离 —— 它是**功能数据**，
    测试写进去的假数据会真的显示在用户的日期面板上。"""
    prod = (Path(__file__).resolve().parent.parent / "database" / "cache" / "holidays").resolve()
    assert settings.holiday_cache_dir.resolve() != prod, (
        f"节假日缓存目录未隔离：{settings.holiday_cache_dir} 是生产目录"
    )
