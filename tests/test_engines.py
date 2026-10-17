# -*- coding: utf-8 -*-
"""Phase 4 Calculation/Warning Engine 纯函数测试。

覆盖：
- 首期：ΔV=None、C=0、Rate=None
- 第二期：ΔV=V2-V1、C=V2-V0、Rate=ΔV/天数
- 缺测跨期：与最近有效值比较、标记 prev_valid_date/skipped_days
- 显式初始值 V0 优先
- 全缺测：V0 未建立
- Warning 双控：累计值/速率 单独与组合触发
"""
from datetime import date

from backend.calculation.engine import PeriodInput, compute_series, latest_valid_result
from backend.warnings.engine import LEVEL_ALARM, LEVEL_CONTROL, RuleLimits, evaluate


def _p(d: str, v: float | None, status: str = "normal") -> PeriodInput:
    y, m, dd = map(int, d.split("-"))
    return PeriodInput(obs_date=date(y, m, dd), value_mm=v, status=status)


def test_first_period_rules():
    """首期：ΔV=None、C=0、Rate=None。"""
    res = compute_series([_p("2026-07-30", 4907.0)])
    assert len(res) == 1
    r = res[0]
    assert r.delta_mm is None
    assert r.cumulative_mm == 0.0
    assert r.rate_mm_per_day is None
    assert r.prev_valid_date is None


def test_second_period_rules():
    """第二期：ΔV=V2−V1、C=V2−V0、Rate=ΔV/Δt。"""
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4906.0)])
    r = res[1]
    assert r.delta_mm == -1.0
    assert r.cumulative_mm == -1.0
    assert r.rate_mm_per_day == -1.0  # 1 天


def test_missing_span_uses_nearest_valid():
    """缺测跨期：与最近有效值比较并标记。"""
    res = compute_series(
        [
            _p("2026-07-30", 4907.0),
            _p("2026-07-31", None),  # 缺测
            _p("2026-08-02", 4905.0),  # 跨过 7-31（缺测）
        ]
    )
    # 缺测期：结果行存在但全 None
    assert res[1].is_missing is True
    assert res[1].cumulative_mm is None
    # 跨期比较：与 7-30（4907）比，Δt=3 天
    r = res[2]
    assert r.delta_mm == -2.0
    assert r.cumulative_mm == -2.0
    assert r.prev_valid_date == date(2026, 7, 30)
    assert r.skipped_days == 3
    assert r.rate_mm_per_day == -2.0 / 3


def test_explicit_initial_value_wins():
    """显式初始值优先于首期读数。"""
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4906.0)], initial_value_mm=4900.0)
    assert res[0].cumulative_mm == 7.0  # 4907-4900，非 0
    assert res[1].cumulative_mm == 6.0


def test_all_missing_v0_not_established():
    res = compute_series([_p("2026-07-30", None), _p("2026-07-31", None)])
    assert all(r.cumulative_mm is None for r in res)


def test_latest_valid_result():
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", None), _p("2026-08-01", 4905.0)])
    latest = latest_valid_result(res)
    assert latest is not None
    assert latest.obs_date == date(2026, 8, 1)
    assert latest.cumulative_mm == -2.0


def test_warning_control_by_cumulative():
    """|C| 落在 [control, alarm) → 预警。"""
    limits = RuleLimits(control_cumulative_mm=30, alarm_cumulative_mm=40)
    # C = 4870-4907 = -37 → |37| ∈ [30,40) → control
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4870.0)])
    ev = evaluate(res[1], limits)
    assert ev["level"] == LEVEL_CONTROL
    # |C| < control → normal
    res2 = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4885.0)])
    assert evaluate(res2[1], limits)["level"] == "normal"


def test_warning_alarm_by_cumulative():
    limits = RuleLimits(control_cumulative_mm=30, alarm_cumulative_mm=40)
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4860.0)])
    ev = evaluate(res[1], limits)
    assert ev["level"] == LEVEL_ALARM
    assert any("累计" in t for t in ev["triggers"])


def test_warning_by_rate():
    limits = RuleLimits(control_rate_mm_per_day=2, alarm_rate_mm_per_day=4)
    # 1 天内变化 5mm → 速率 5 → 报警
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4902.0)])
    ev = evaluate(res[1], limits)
    assert ev["level"] == LEVEL_ALARM
    assert any("速率" in t for t in ev["triggers"])


def test_warning_by_delta_when_rate_is_diluted():
    """ΔV 这一档存在的理由：**隔得久的观测会把速率摊薄，ΔV 不会**（用户 2026-10-16 要的"每期变化上限"）。

    隔 3 天跳 6mm → 速率只有 2mm/d，没超 3；但单期实实在在跳了 6mm。
    只按速率判，这里一声不响；填了 ΔV 上限 5 → 判预警，且触发语点名「本次变化量」。
    """
    limits = RuleLimits(control_rate_mm_per_day=3, control_delta_mm=5)
    res = compute_series([_p("2026-07-28", 4907.0), _p("2026-07-31", 4901.0)])
    assert res[1].skipped_days == 3
    ev = evaluate(res[1], limits)
    assert ev["level"] == LEVEL_CONTROL
    assert ev["rate_check"] == "normal", "速率被摊薄，这一维不该触发"
    assert ev["triggers"] == ["本次变化量达到控制值"]


def test_delta_empty_means_that_dimension_not_judged():
    """ΔV 两格都留空 → 这一维完全不参与判定（与累计/速率同一口径，不拿 0 当阈值）。"""
    limits = RuleLimits(control_cumulative_mm=30, alarm_cumulative_mm=40)
    # 单期跳 25mm（累计才 25，未到控制 30）→ 只填了累计/速率时就是 normal
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4882.0)])
    ev = evaluate(res[1], limits)
    assert ev["level"] == "normal"
    assert ev["delta_check"] == "normal"
    # 同一份数据把 ΔV 上限收到 20 → 立刻出报警，证明"填了才判"确实生效
    assert evaluate(res[1], RuleLimits(alarm_delta_mm=20))["level"] == LEVEL_ALARM


def test_warning_normal():
    limits = RuleLimits(control_cumulative_mm=30, alarm_cumulative_mm=40)
    res = compute_series([_p("2026-07-30", 4907.0), _p("2026-07-31", 4906.0)])
    ev = evaluate(res[1], limits)
    assert ev["level"] == "normal"
    assert ev["triggers"] == []


def test_missing_period_no_warning():
    limits = RuleLimits(control_cumulative_mm=30)
    res = compute_series([_p("2026-07-30", None)])
    ev = evaluate(res[0], limits)
    assert ev["level"] == "normal"
