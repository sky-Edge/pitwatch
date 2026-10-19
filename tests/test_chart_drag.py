# -*- coding: utf-8 -*-
"""曲线拖拽调整：数据侧要撑得住"拖一个点 → 改成那天的读数"。

用户 2026-10-19：「我想在曲线图拖拽调整数值…如果手动调整超过预警就不能动了」

前端做拖拽和限位，但**数据必须对得上**，否则拖完落库会改错值：
  ① 每个点要带 `record_id`（改哪条记录）和 `value_mm`（原读数）；
  ② 要带 `limits`（预警/报警的累计阈值），前端才能算出"能拖到哪"；
  ③ 没配规则时 limits 必须是 None —— 没有边界就不该让用户拖。
"""
from __future__ import annotations

import pytest

RULES = [
    {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30.0,
     "threshold_rate_mm_per_day": 10.0, "is_active": True},
    {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40.0,
     "threshold_rate_mm_per_day": 20.0, "is_active": True},
]


def _rules(client, pid: int) -> None:
    r = client.put(f"/api/v1/projects/{pid}/warning-rules", json={"rules": RULES})
    assert r.status_code == 200, r.text


# ─────────────────────────── 脚手架 ───────────────────────────


def _mk(client, code: str = "PRJ-D1", name: str = "拖拽一号", v0: float = 5000.0) -> tuple[int, int]:
    pid = client.post("/api/v1/projects", json={"code": code, "name": name}).json()["id"]
    p = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": v0},
    ).json()
    return pid, p["id"]


def _rec(client, point_id: int, obs_date: str, value_mm: float) -> int:
    r = client.post(
        f"/api/v1/points/{point_id}/records",
        json={
            "series_type": "settlement",
            "component": "value",
            "obs_date": obs_date,
            "value_mm": value_mm,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _chart(client, pid: int) -> dict:
    r = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement", "component": "value"})
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────────────────── 拖拽需要的数据 ───────────────────────────


def test_each_point_carries_record_id_and_raw_value(client) -> None:
    """每个点必须能回答"改哪条记录、原来多少" —— 否则前端没法把目标累计换算成读数。"""
    pid, pt = _mk(client, v0=5000.0)
    rid = _rec(client, pt, "2026-07-30", 5000.0)
    _rec(client, pt, "2026-07-31", 5003.5)

    d = _chart(client, pid)
    data = d["series"][0]["data"]
    assert data[0]["record_id"] == rid
    assert data[0]["value_mm"] == pytest.approx(5000.0)
    assert data[0]["cumulative_mm"] == pytest.approx(0.0)
    assert data[1]["cumulative_mm"] == pytest.approx(3.5)
    assert all(p["record_id"] for p in data)


def test_limits_are_returned_for_drag_bounds(client) -> None:
    """阈值要随曲线一起回来 —— 前端靠它把拖动夹在 ±预警值 内。"""
    pid, pt = _mk(client, code="PRJ-D2", name="拖拽二号")
    _rec(client, pt, "2026-07-30", 5000.0)
    _rules(client, pid)  # control 30 / alarm 40

    d = _chart(client, pid)
    assert d["limits"]["control_cumulative_mm"] == pytest.approx(30.0)
    assert d["limits"]["alarm_cumulative_mm"] == pytest.approx(40.0)


def test_limits_are_null_when_no_rules(client) -> None:
    """没配规则 → limits 为 null。前端据此**禁用拖拽**，而不是让人随便拖。"""
    pid, pt = _mk(client, code="PRJ-D3", name="拖拽三号")
    _rec(client, pt, "2026-07-30", 5000.0)

    d = _chart(client, pid)
    assert d["limits"]["control_cumulative_mm"] is None
    assert d["limits"]["alarm_cumulative_mm"] is None


# ─────────────────────────── 拖完之后确实改了 ───────────────────────────


def test_correcting_by_cumulative_changes_the_curve(client) -> None:
    """模拟前端换算：想让累计从 0 → 20，就把读数改成 V0+20，曲线应跟着变。

    前端算的是 `新读数 = 原读数 − 原累计 + 目标累计`；这里用同一个式子验证一遍，
    保证"拖到 20"落库后曲线上真的是 20。
    """
    pid, pt = _mk(client, code="PRJ-D4", name="拖拽四号", v0=5000.0)
    rid = _rec(client, pt, "2026-07-30", 5000.0)
    _rec(client, pt, "2026-07-31", 5012.0)

    before = _chart(client, pid)["series"][0]["data"]
    first = before[0]
    target = 20.0
    new_value = first["value_mm"] - first["cumulative_mm"] + target

    r = client.post(f"/api/v1/records/{rid}/correct", json={"value_mm": new_value, "remark": "曲线图手动调整"})
    assert r.status_code == 200, r.text

    after = _chart(client, pid)["series"][0]["data"]
    assert after[0]["cumulative_mm"] == pytest.approx(target)


def test_original_record_is_kept_after_correction(client) -> None:
    """更正只增不改：原记录必须还在（标记 superseded），不能真删。

    这是全系统的铁律，曲线拖拽属于"改真实数据"，同样受它约束。
    """
    pid, pt = _mk(client, code="PRJ-D5", name="拖拽五号", v0=5000.0)
    rid = _rec(client, pt, "2026-07-30", 5000.0)

    client.post(f"/api/v1/records/{rid}/correct", json={"value_mm": 5006.0, "remark": "测试"})

    all_recs = client.get(f"/api/v1/points/{pt}/records", params={"active_only": False}).json()
    old = next((x for x in all_recs if x["id"] == rid), None)
    assert old is not None, "原记录被删了 —— 违反只增不改"
    assert old["status"] == "superseded"
    assert old["value_mm"] == pytest.approx(5000.0), "原值必须保留"


def test_corrected_point_reflects_new_warning_level(client) -> None:
    """把值拖到预警线以内/以外，重新取数时预警等级要跟着变 —— 否则拖完还是旧标记。"""
    pid, pt = _mk(client, code="PRJ-D6", name="拖拽六号", v0=5000.0)
    rid = _rec(client, pt, "2026-07-30", 5000.0)
    _rec(client, pt, "2026-07-31", 5035.0)  # 累计 35 → 已超 control(30)
    _rules(client, pid)

    d = _chart(client, pid)
    assert d["warning_points"], "35mm 应该被判成预警"

    # 拖回 5mm —— 超限的是**第二期**（35mm），所以要改它那条记录。
    # ⚠ 别改成 5010：累计 10mm 虽低于 30，但**变化速率正好 10mm/天**，
    #    撞上 rate 限值（判级是「累计或速率任一超标」），预警点照样在。
    second = d["series"][0]["data"][1]
    client.post(
        f"/api/v1/records/{second['record_id']}/correct",
        json={"value_mm": 5005.0, "remark": "调回"},
    )

    d2 = _chart(client, pid)
    assert d2["warning_points"] == [], "调整后不该还有预警点"
