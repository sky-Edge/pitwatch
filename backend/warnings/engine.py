# -*- coding: utf-8 -*-
"""Warning Engine —— 纯函数，不碰 DB/API。

输入：计算结果 + 预警规则（control/alarm 双控）。
输出：预警等级 normal / control(预警) / alarm(报警)。

判断（双控，任一超标即触发，等级取高）：
- |累计变化量|   ≥ alarm.threshold_cumulative_mm → alarm；≥ control → control
- |本次变化量 ΔV| ≥ alarm.threshold_delta_mm      → alarm；≥ control → control
- |变化速率|     ≥ alarm.threshold_rate_mm_per_day → alarm；≥ control → control

三维都是"填了才判这一维"：某维两个阈值都是 None → 该维不判（绝不拿 0 当阈值，
也绝不因为另一维有值就把它顶上去）。ΔV 与速率的分工：ΔV 不看观测间隔，
隔几天测都按同一个上限卡；速率 = ΔV/天数，间隔一长就被摊薄。
"""
from dataclasses import dataclass

from backend.calculation.engine import PeriodResult

LEVEL_NORMAL = "normal"
LEVEL_CONTROL = "control"
LEVEL_ALARM = "alarm"


@dataclass(frozen=True)
class RuleLimits:
    control_cumulative_mm: float | None = None
    #: 本次变化量 ΔV 上限（2026-10-16 加）。表上**不印**这一格，只参与判定与 Word 文案。
    control_delta_mm: float | None = None
    control_rate_mm_per_day: float | None = None
    alarm_cumulative_mm: float | None = None
    alarm_delta_mm: float | None = None
    alarm_rate_mm_per_day: float | None = None


def check_level(value: float | None, control: float | None, alarm: float | None) -> int:
    """返回 0=正常 1=预警 2=报警。

    **公开函数**：导出文件检查（export_audit_service）要按同一口径判
    "报表上印的数超了没有"，判定规则必须只有这一处 —— 一处用 > 、一处用 >= ，
    就会得出正好相反的结论。
    """
    if value is None or (control is None and alarm is None):
        return 0
    v = abs(value)
    if alarm is not None and v >= alarm:
        return 2
    if control is not None and v >= control:
        return 1
    return 0


def evaluate(result: PeriodResult, limits: RuleLimits) -> dict:
    """对单期结果做双控判断（三维取高）。"""
    cum = check_level(result.cumulative_mm, limits.control_cumulative_mm, limits.alarm_cumulative_mm)
    delta = check_level(result.delta_mm, limits.control_delta_mm, limits.alarm_delta_mm)
    rate = check_level(result.rate_mm_per_day, limits.control_rate_mm_per_day, limits.alarm_rate_mm_per_day)
    level = max(cum, delta, rate)

    triggers: list[str] = []
    if cum == 2:
        triggers.append("累计变化量达到报警值")
    elif cum == 1:
        triggers.append("累计变化量达到控制值")
    if delta == 2:
        triggers.append("本次变化量达到报警值")
    elif delta == 1:
        triggers.append("本次变化量达到控制值")
    if rate == 2:
        triggers.append("变化速率达到报警值")
    elif rate == 1:
        triggers.append("变化速率达到控制值")

    return {
        "level": (LEVEL_NORMAL, LEVEL_CONTROL, LEVEL_ALARM)[level],
        "cumulative_check": (LEVEL_NORMAL, LEVEL_CONTROL, LEVEL_ALARM)[cum],
        "delta_check": (LEVEL_NORMAL, LEVEL_CONTROL, LEVEL_ALARM)[delta],
        "rate_check": (LEVEL_NORMAL, LEVEL_CONTROL, LEVEL_ALARM)[rate],
        "triggers": triggers,
    }
