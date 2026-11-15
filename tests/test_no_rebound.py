# -*- coding: utf-8 -*-
"""禁止回弹（沉降只沉不升）。

用户 2026-11-15：盖梁支架这类沉降观测理论上只沉不升，测出来的"回升"是测量误差，
曲线不该掉头。开关放在**预警规则**里（项目 → 规则套 → 该指标任一级勾了即生效）。

守的几条：
- 勾上之后累计变化的绝对值不再变小，ΔV=0、速率=0（曲线持平，不是掉头）；
- **原始读数照旧存库**，记录页查到的还是实测值 —— 改的只是成果口径；
- 没勾的项目行为与改造前逐字一致（回归）；
- 水平位移不参与（有来有回是真实变形，抹平等于抹掉数据）；
- 报表三列自洽：(本次测值 − 上次测值)×1000 == 本次变化量。
"""
from datetime import date

import pytest

from backend.calculation.engine import PeriodInput, compute_series
from backend.core.db import SessionLocal
from backend.reports.template_excel import series_rows_for_date
from tests.conftest import put_control_rules

#: 100.0 起测：沉 0.2 → 回升 0.2（测量误差）→ 继续沉到 0.5
REBOUND = [
    PeriodInput(date(2026, 5, 1), 100.00),
    PeriodInput(date(2026, 5, 2), 99.80),
    PeriodInput(date(2026, 5, 3), 100.00),
    PeriodInput(date(2026, 5, 4), 99.50),
]

NO_REBOUND_RULES = [
    {
        "indicator": "settlement",
        "level": "control",
        "threshold_cumulative_mm": 30.0,
        "threshold_rate_mm_per_day": 10.0,
        "no_rebound": True,
        "is_active": True,
    },
    {
        "indicator": "displacement",
        "level": "control",
        "threshold_cumulative_mm": 20.0,
        "threshold_rate_mm_per_day": 3.0,
        "is_active": True,
    },
]


def _mk_project(client, name: str, *, no_rebound: bool, point_type: str = "settlement") -> tuple[int, int]:
    r = client.post("/api/v1/projects", json={"code": name, "name": name})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "GC01", "point_type": point_type, "initial_value_mm": 100.0},
    )
    assert r.status_code == 201, r.text
    pt_id = r.json()["id"]
    put_control_rules(client, pid, NO_REBOUND_RULES if no_rebound else None)
    return pid, pt_id


def _add(client, pt_id: int, day: str, value: float, series_type: str = "settlement", comp: str = "value") -> None:
    r = client.post(
        f"/api/v1/points/{pt_id}/records",
        json={
            "series_type": series_type,
            "component": comp,
            "obs_date": day,
            "value_mm": value,
        },
    )
    assert r.status_code == 201, r.text


# ─────────────── 引擎层（纯函数） ───────────────


def test_engine_flattens_rebound_when_enabled():
    rows = compute_series(REBOUND, 100.0, no_rebound=True)

    assert [r.cumulative_mm for r in rows] == pytest.approx([0.0, -0.2, -0.2, -0.5])
    # 回升那期被按"没变化"处理
    assert rows[2].delta_mm == pytest.approx(0.0)
    assert rows[2].rate_mm_per_day == pytest.approx(0.0)
    assert rows[2].effective_value_mm == pytest.approx(99.8)
    # 下一期继续沉，累计接着往下走（不是从上期的实测值算，而是从成果值算）
    assert rows[3].delta_mm == pytest.approx(-0.3)


def test_engine_keeps_rebound_when_disabled():
    """不勾的项目必须与改造前逐字一致 —— 这是回归线。"""
    rows = compute_series(REBOUND, 100.0)

    assert [r.cumulative_mm for r in rows] == pytest.approx([0.0, -0.2, 0.0, -0.5])
    assert rows[2].delta_mm == pytest.approx(0.2)
    assert rows[2].effective_value_mm == pytest.approx(100.0)


def test_engine_outputs_stay_self_consistent():
    """ΣΔV == 最新累计；成果读数 − V0 == 累计 —— 报表靠这两条才自洽。"""
    rows = compute_series(REBOUND, 100.0, no_rebound=True)
    deltas = [r.delta_mm for r in rows if r.delta_mm is not None]
    assert sum(deltas) == pytest.approx(rows[-1].cumulative_mm)
    for r in rows:
        if not r.is_missing:
            assert r.effective_value_mm - 100.0 == pytest.approx(r.cumulative_mm)


def test_engine_ignores_missing_periods_as_baseline():
    """缺测期不参与基准：上一期缺测时，本期与更早的有效期比。"""
    rows = compute_series(
        [
            PeriodInput(date(2026, 5, 1), 100.0),
            PeriodInput(date(2026, 5, 2), 99.8),
            PeriodInput(date(2026, 5, 3), None),
            PeriodInput(date(2026, 5, 4), 99.9),
        ],
        100.0,
        no_rebound=True,
    )
    assert rows[2].is_missing and rows[2].effective_value_mm is None
    # 5-4 的 99.9 相对 5-2 的 99.8 是"回升"，被抹平
    assert rows[3].cumulative_mm == pytest.approx(-0.2)
    assert rows[3].skipped_days == 2


# ─────────────── 规则 → 计算 → 报表（整条路） ───────────────


def test_rule_flag_reaches_calculated_series(client):
    """规则里勾了 → 接口算出来的累计不再回升。"""
    _pid, pt = _mk_project(client, "盖梁支架", no_rebound=True)
    for day, v in (("2026-05-01", 100.0), ("2026-05-02", 99.8), ("2026-05-03", 100.0)):
        _add(client, pt, day, v)

    rows = client.get(
        f"/api/v1/points/{pt}/computed", params={"series_type": "settlement", "component": "value"}
    ).json()
    assert [r["cumulative_mm"] for r in rows] == pytest.approx([0.0, -0.2, -0.2], abs=1e-6)


def test_missing_flag_keeps_rebound(client):
    """没勾的项目照旧能回升 —— 基坑这类有真实回弹的场景不能被误伤。"""
    _pid, pt = _mk_project(client, "普通基坑", no_rebound=False)
    for day, v in (("2026-05-01", 100.0), ("2026-05-02", 99.8), ("2026-05-03", 100.0)):
        _add(client, pt, day, v)

    rows = client.get(
        f"/api/v1/points/{pt}/computed", params={"series_type": "settlement", "component": "value"}
    ).json()
    assert [r["cumulative_mm"] for r in rows] == pytest.approx([0.0, -0.2, 0.0], abs=1e-6)


def test_original_reading_is_untouched(client):
    """原始读数只增不改：记录页查到的还是实测值（99.8 → 100.0）。"""
    pid, pt = _mk_project(client, "盖梁支架", no_rebound=True)
    for day, v in (("2026-05-01", 100.0), ("2026-05-02", 99.8), ("2026-05-03", 100.0)):
        _add(client, pt, day, v)

    recs = client.get(f"/api/v1/points/{pt}/records").json()
    by_day = {r["obs_date"]: r["value_mm"] for r in recs}
    assert by_day["2026-05-03"] == pytest.approx(100.0)


def test_record_page_shows_real_reading_not_flattened():
    """点开记录页看到的是实测值；被抹平的只是「累计/本次变化」这一层成果口径。"""
    from backend.services import calc_service
    from backend.services.point_service import get_point

    rows = compute_series(REBOUND, 100.0, no_rebound=True)
    # 引擎不改输入：value_mm 仍在 PeriodInput 里，成果读数另存一列
    assert [p.value_mm for p in REBOUND] == [100.0, 99.8, 100.0, 99.5]
    assert rows[2].effective_value_mm == pytest.approx(99.8)


def test_report_columns_match_after_flattening(client):
    """报表「本次测值 − 上次测值」必须等于「本次变化量」，哪怕本期被抹平。"""
    pid, pt = _mk_project(client, "盖梁支架", no_rebound=True)
    for day, v in (("2026-05-01", 100.0), ("2026-05-02", 99.8), ("2026-05-03", 100.0), ("2026-05-04", 99.5)):
        _add(client, pt, day, v)

    db = SessionLocal()
    try:
        for day in ("2026-05-02", "2026-05-03", "2026-05-04"):
            rows = series_rows_for_date(db, pid, date.fromisoformat(day))
            assert len(rows) == 1
            row = rows[0]
            assert row["current_m"] - row["prev_m"] == pytest.approx(row["delta_mm"] / 1000, abs=1e-9)
            assert row["current_m"] - row["v0_m"] == pytest.approx(row["cum_mm"] / 1000, abs=1e-9)
    finally:
        db.close()


def test_displacement_is_never_flattened(client):
    """水平位移：勾了也不抹 —— 位移有来有回是真实变形，抹平等于抹掉现场情况。"""
    pid, pt = _mk_project(client, "盖梁支架", no_rebound=True, point_type="displacement")
    for day, v in (("2026-05-01", 100.0), ("2026-05-02", 98.0), ("2026-05-03", 99.0)):
        _add(client, pt, day, v, series_type="displacement", comp="X")

    rows = client.get(
        f"/api/v1/points/{pt}/computed", params={"series_type": "displacement", "component": "X"}
    ).json()
    # 位移照旧回升：-2.0 → -1.0
    assert [r["cumulative_mm"] for r in rows] == pytest.approx([0.0, -2.0, -1.0], abs=1e-6)


def test_rule_roundtrip_keeps_flag(client):
    """规则写进去能读回来 —— 否则界面勾了、重开又变没勾。"""
    pid, _pt = _mk_project(client, "盖梁支架", no_rebound=True)

    rules = client.get(f"/api/v1/projects/{pid}/warning-rules").json()
    rows = rules["rules"] if isinstance(rules, dict) else rules
    settle = [r for r in rows if r["indicator"] == "settlement"]
    assert settle and all(r["no_rebound"] for r in settle)


def test_period_number_in_chinese_past_one_hundred():
    """期次过了 100 也要中文 —— 实测北侧钢栈桥到 113 期时，报表上一处阿拉伯数字、一处中文。"""
    from backend.reports.master_report import _cn_period

    assert _cn_period(113) == "一百一十三"
    assert _cn_period(100) == "一百"
    assert _cn_period(105) == "一百零五"
    assert _cn_period(10) == "十"
    assert _cn_period(21) == "二十一"
