# -*- coding: utf-8 -*-
"""Calculation Engine —— 纯函数，不碰 DB/API。

输入：某测点某分量的按日期升序的生效记录序列。
输出：每期的 本次变化(ΔV) / 累计变化(C) / 变化速率(Rate)，含跨期标记。

规则（用户已确认）：
- V0 = 显式初始值 initial_value_mm（若有），否则 = 首期有效读数。
- ΔV = Vn − V(最近有效值)；首期或此前无有效值 → None；跨期记 prev_valid_date/skipped_days。
- C   = Vn − V0；首期 → 0；V0 尚未建立（全部缺测）→ None。
- Rate = ΔV / Δt（天）；ΔV 为 None → None。
- 缺测期：结果行仍生成，is_missing=True，各值为 None，不作为后续比较基准。

确定性：相同输入必得相同输出（可复算）。
"""
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class PeriodInput:
    obs_date: date
    value_mm: float | None  # None = 缺测
    status: str = "normal"


@dataclass
class PeriodResult:
    obs_date: date
    is_missing: bool
    delta_mm: float | None  # 本次变化
    cumulative_mm: float | None  # 累计变化
    rate_mm_per_day: float | None
    prev_valid_date: date | None  # 跨期标记：与最近有效值比较的日期
    skipped_days: int | None  # 距最近有效值的天数

    def to_dict(self) -> dict:
        return {
            "obs_date": self.obs_date.isoformat(),
            "is_missing": self.is_missing,
            "delta_mm": self.delta_mm,
            "cumulative_mm": self.cumulative_mm,
            "rate_mm_per_day": self.rate_mm_per_day,
            "prev_valid_date": self.prev_valid_date.isoformat() if self.prev_valid_date else None,
            "skipped_days": self.skipped_days,
        }


def compute_series(
    periods: list[PeriodInput],
    initial_value_mm: float | None = None,
) -> list[PeriodResult]:
    """按日期升序计算整条序列。periods 需已按 obs_date 升序且为生效记录。"""
    results: list[PeriodResult] = []
    v0: float | None = initial_value_mm  # 显式初始值优先
    v0_established: bool = initial_value_mm is not None
    last_valid: PeriodInput | None = None  # 最近一次有效记录（跨期基准）

    for p in periods:
        if p.value_mm is None or p.status == "void":
            # 缺测/作废期：结果行占位，不参与后续基准
            results.append(
                PeriodResult(
                    obs_date=p.obs_date,
                    is_missing=True,
                    delta_mm=None,
                    cumulative_mm=None,
                    rate_mm_per_day=None,
                    prev_valid_date=last_valid.obs_date if last_valid else None,
                    skipped_days=None,
                )
            )
            continue

        # 首期有效读数自动成为 V0（若未显式指定）
        if not v0_established:
            v0 = p.value_mm
            v0_established = True

        # 本次变化：与最近有效值比较（跨期支持）
        if last_valid is not None and last_valid.value_mm is not None:
            delta = p.value_mm - last_valid.value_mm
            skipped_days = (p.obs_date - last_valid.obs_date).days
        else:
            delta = None
            skipped_days = None

        # 累计变化：相对 V0。
        # 自动模式（V0=首期读数）下首期 natural 为 0；显式初始值模式下首期 = V1−V0。
        cumulative = (p.value_mm - v0) if v0_established else None

        # 速率：ΔV / Δt
        rate = None
        if delta is not None and skipped_days is not None and skipped_days > 0:
            rate = delta / skipped_days

        results.append(
            PeriodResult(
                obs_date=p.obs_date,
                is_missing=False,
                delta_mm=delta,
                cumulative_mm=cumulative,
                rate_mm_per_day=rate,
                prev_valid_date=last_valid.obs_date if last_valid else None,
                skipped_days=skipped_days,
            )
        )
        last_valid = p

    return results


def latest_valid_result(results: list[PeriodResult]) -> PeriodResult | None:
    """取最新一期有值的结果（缺测期会返回 None 之外的查找）。"""
    for r in reversed(results):
        if not r.is_missing:
            return r
    return None
