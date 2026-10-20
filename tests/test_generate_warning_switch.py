# -*- coding: utf-8 -*-
"""主界面开关：自动生成数据时是否允许产生预警/报警。

用户 2026-10-20：「在主界面加个开关，是否打开自动生成产生报警值」

开关是**全站**的（存 app_settings），影响以后所有生成 —— 不是某个项目的属性。
关掉后生成的数据只会在预警线以内波动，用来造"干净的演示数据"，
避免一生成就满屏红黄点。
"""
from __future__ import annotations

import pytest


def _setup(client, code: str, name: str, *, rate=3.0, cum=20.0) -> int:
    # 故意用**很紧**的限值（1mm/d、5mm 累计）：噪声本身约 ±2.3mm，
    # 这样开必然触发预警、关必须一条都没有，两边的判据才立得住。
    # 用默认的 3/20 时噪声根本够不着限值，两边都是 0，测了等于没测。
    pid = client.post("/api/v1/projects", json={"code": code, "name": name}).json()["id"]
    client.post(
        f"/api/v1/projects/{pid}/points",
        # 必须给初始值：没有 V0 的测点，生成器会**静默返回 0 条**（既有行为），
        # 测试就会因为"没生成数据"而假通过 —— 我第一版就栽在这儿。
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 5000.0},
    )
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control",
                 "threshold_cumulative_mm": cum, "threshold_rate_mm_per_day": rate},
                {"indicator": "settlement", "level": "alarm",
                 "threshold_cumulative_mm": cum + 1, "threshold_rate_mm_per_day": rate + 1},
            ]
        },
    )
    return pid


def _set_switch(client, on: bool) -> None:
    r = client.put("/api/v1/settings", json={"generate_allow_warning": on})
    assert r.status_code == 200, r.text


def _generate(client, pid: int, days: int = 40, **params) -> dict:
    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-07-01", "end": f"2026-08-{days - 31:02d}" if days > 31 else "2026-07-31",
                "seed": 42, **params},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _warning_count(client, pid: int) -> int:
    """数"有多少期被判定为预警/报警"。

    ⚠ 不能查 `warning-events` 表：那是**派生表**，要在别处触发重算才会更新；
    生成完数据立刻查它永远是 0，测试会假通过（我第一版就是这么写的）。
    直接走 `/computed`（每次实时算 `evaluate()`），才是真正的判据。
    """
    total = 0
    points = client.get(f"/api/v1/projects/{pid}/points").json()
    for pt in points:
        rows = client.get(
            f"/api/v1/points/{pt['id']}/computed",
            params={"series_type": "settlement", "component": "value"},
        ).json()
        total += sum(1 for r in rows if r["warning_level"] != "normal")
    return total


# ─────────────────────────── 开关本身 ───────────────────────────


def test_switch_defaults_to_on(client) -> None:
    """默认开着 —— 生成的数据要像真实台账（偶尔触发预警），这是原本的行为。"""
    s = client.get("/api/v1/settings").json()
    assert s["generate_allow_warning"] is True


def test_switch_roundtrips(client) -> None:
    _set_switch(client, False)
    assert client.get("/api/v1/settings").json()["generate_allow_warning"] is False
    _set_switch(client, True)
    assert client.get("/api/v1/settings").json()["generate_allow_warning"] is True


# ─────────────────────────── 关掉之后真的不产生预警 ───────────────────────────


def test_generated_data_has_no_warning_when_switch_off(client) -> None:
    """关掉开关 → 生成 60 期数据，预警事件必须是 0。

    这是这个功能的全部意义：用户想要一份"干净"的演示数据。
    如果还冒红黄点，开关就是假的。
    """
    pid = _setup(client, "PRJ-G1", "关预警一号")
    _set_switch(client, False)
    _generate(client, pid, days=60)

    assert _warning_count(client, pid) == 0, "关掉开关后仍然生成了预警事件"


def test_generated_data_can_have_warning_when_switch_on(client) -> None:
    """开着开关 → 同样的种子、同样的范围，应当出现预警（否则说明限幅被误改）。"""
    pid = _setup(client, "PRJ-G2", "开预警二号")
    _set_switch(client, True)
    _generate(client, pid, days=60)

    assert _warning_count(client, pid) > 0, "开着开关却一条预警都没有，限幅可能被误收紧"


def test_explicit_param_overrides_switch(client) -> None:
    """单次调用可以显式覆盖全局开关（不写进设置）。"""
    pid = _setup(client, "PRJ-G3", "覆盖三号")
    _set_switch(client, True)
    _generate(client, pid, days=60, allow_warning=False)

    assert _warning_count(client, pid) == 0, "allow_warning=false 没生效"
    # 全局设置不该被这次调用改掉
    assert client.get("/api/v1/settings").json()["generate_allow_warning"] is True


def test_switch_off_does_not_touch_existing_records(client) -> None:
    """关掉开关只影响**之后的生成**，已有数据一条都不能变。"""
    pid = _setup(client, "PRJ-G4", "不改旧数四号")
    _set_switch(client, True)
    _generate(client, pid, days=40)

    before = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"}).json()
    before_vals = [(p["date"], p["cumulative_mm"]) for s in before["series"] for p in s["data"]]

    _set_switch(client, False)

    after = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"}).json()
    after_vals = [(p["date"], p["cumulative_mm"]) for s in after["series"] for p in s["data"]]

    assert after_vals == before_vals, "切换开关把已有数据改了 —— 它只该影响以后的生成"
