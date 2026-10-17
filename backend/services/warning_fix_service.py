# -*- coding: utf-8 -*-
"""一键修正报警数据（2026-09-29 用户需求）。

【要解决什么】
项目里出现了触发预警/报警的观测值，用户希望"一键"把它们修正回限值以内 ——
典型场景是导入或生成的演示数据超出控制值，一条条点进去改太费事。

【为什么不能直接改值】
`observation_records` **只增不改**是项目的硬约束（原值必须可追溯）。
所以每一次修正都走 `observation_repo.correct()`：
旧记录置 `superseded` 并指向新记录，新记录 `source='correct'`、`remark` 记下「原值 → 新值」。
**绝不物理删除，也绝不覆盖旧值。**

【修正逻辑：逐期滚动，最小改动】
对每个 (测点, 分量) 的生效序列按日期升序走一遍，与 Calculation/Warning Engine 同口径：

- 只改**真正触发**的那一期（没超限的一期一动不动 —— 这才是"最小改动"）；
- 改完的值会成为下一期的比较基准，所以后面的期要**基于修正后的序列**重新判断
  （否则会出现"改完第 5 期，第 6 期又报警"）；
- 安全值一律留 10% 余量：判定是 `|值| ≥ 阈值 → 触发`，贴着阈值改等于没改；
- 读数若是整 mm（现场读数就是 1mm 精度），修正步长**向下取整**，取整方向也只会更安全。

【改不动的情况要如实报告，不能假装成功】
- 该指标没有配置任何限值 → 无从定义"安全值"，跳过；
- 累计已经超限、而单期速率上限又不允许一步拉回来 → 跳过并说明原因。
（正常序列不会走到第二种：逐期滚动时累计在更早的期就已经被守住了。）

【口径必须与预警引擎一致】
限值取 `warning_event_service._limits_for()`，判定取 `warnings.engine.evaluate()`，
计算取 `calculation.engine.compute_series()` —— **一处都不另写**，
否则会出现"修正完还报警"或"界面上没报警却被改"这种自相矛盾。
"""
from __future__ import annotations

import math
from sqlalchemy.orm import Session

from backend.calculation.engine import PeriodInput, compute_series
from backend.repositories import observation_repo
from backend.services.point_service import list_points
from backend.services.warning_event_service import _limits_for, _series_records
from backend.warnings.engine import RuleLimits, evaluate

# 安全余量：判定是 `>=`，贴着阈值改等于没改；同时给浮点留点空间。
_SAFETY_MARGIN = 0.9

# 报告里最多给多少条「旧值 → 新值」对照（与数据生成器保持同一个口味）
_MAX_SAMPLES = 20


def _safe_limits(limits: RuleLimits) -> tuple[float | None, float | None, float | None]:
    """(单期速率上限, 单期变化量 ΔV 上限, 累计上限)。control 缺失时退回 alarm —— 否则无从定义"安全"。"""
    rate = limits.control_rate_mm_per_day or limits.alarm_rate_mm_per_day
    delta = limits.control_delta_mm or limits.alarm_delta_mm
    cum = limits.control_cumulative_mm or limits.alarm_cumulative_mm
    return rate, delta, cum


def _fix_value(
    *,
    raw_value: float,
    prev_value: float,
    v0: float | None,
    skipped_days: int | None,
    rate_limit: float | None,
    delta_limit: float | None,
    cum_limit: float | None,
    integral: bool,
) -> float:
    """算一个「尽量少动、但一定安全」的新读数。

    两步限幅，顺序不能反：
    ① 先压单期上限 ——「速率 × 间隔天数」和「本次变化量 ΔV」谁更严按谁（ΔV 不看间隔，
       隔 3 天测一次时速率会被摊薄，只有 ΔV 还卡得住）；
    ② 再按累计压（这是"相对 V0"的约束）——
       压累计可能又冲破单期上限，所以 ② 之后要**再走一遍 ①**。
    """
    def quantize(step: float) -> float:
        """步长取整：整 mm 的数据取整数步，且**向下取整**（只会更安全）。"""
        if integral:
            return float(math.floor(step))
        return math.floor(step * 1000.0) / 1000.0

    def cap_step(step: float) -> float:
        allowed: list[float] = []
        if rate_limit is not None and skipped_days:
            allowed.append(rate_limit * skipped_days)
        if delta_limit is not None:
            allowed.append(delta_limit)
        if not allowed:
            return step
        lim = min(allowed) * _SAFETY_MARGIN
        return math.copysign(quantize(lim), step) if abs(step) > lim else step

    step = raw_value - prev_value

    # ① 单期上限（速率 / ΔV 取严）
    step = cap_step(step)

    # ② 累计上限
    if cum_limit is not None and v0 is not None:
        safe_cum = cum_limit * _SAFETY_MARGIN
        landing = prev_value + step
        if abs(landing - v0) > safe_cum:
            step = (v0 + math.copysign(safe_cum, landing - v0)) - prev_value
            # ② 之后回到 ① 复核：拉累计的那一步本身也不能超单期上限
            step = cap_step(step)

    return prev_value + step


def _scan_series(db: Session, point, series_type: str, component: str,
                 limits: RuleLimits, include_control: bool) -> tuple[list[dict], list[dict]]:
    """扫一条序列，返回 (可修正清单, 改不动的清单)。只读，不落库。

    ⚠ **必须迭代到收敛**（这是实测踩出来的）：
    把第 5 期往下压之后，第 6 期相对"新的上一期"的变化量就变大了 —— 本来正常的一期会变成超限。
    单趟扫描只会看**原始**序列，判不出这种"涟漪"，结果是"点一次修好 118 条、还剩 18 条"，
    用户得反复点。所以这里在内存里反复算、反复修，直到某一趟没有新改动为止。

    每一趟都调 `compute_series()` / `evaluate()`（口径与预警引擎完全一致），
    不另写一套 delta/累计/速率的算法 —— 两套算法必然会在某个边界上打架。
    """
    records = _series_records(db, point.id, series_type, component)
    if not records:
        return [], []

    rate_limit, delta_limit, cum_limit = _safe_limits(limits)
    v0 = point.initial_value_mm
    if v0 is None:
        v0 = next((r.value_mm for r in records if r.value_mm is not None), None)

    values: dict[int, float | None] = {r.id: r.value_mm for r in records}   # 内存中修好的值
    planned: dict[int, dict] = {}

    def snapshot() -> list[dict]:
        periods = [PeriodInput(obs_date=r.obs_date, value_mm=values[r.id], status=r.status)
                   for r in records]
        return list(zip(records, compute_series(periods, point.initial_value_mm)))

    converged = False
    for _ in range(len(records) + 8):      # 涟漪最长就是整条序列那么长
        pending = 0
        prev_value: float | None = None
        for rec, row in snapshot():
            if rec.value_mm is None or row.is_missing:
                continue                    # 缺测期只占位，不作为比较基准
            level = evaluate(row, limits)["level"]
            if level == "normal" or (not include_control and level != "alarm"):
                prev_value = values[rec.id]
                continue
            if rate_limit is None and cum_limit is None:
                prev_value = values[rec.id]     # 没有限值 → 无从修正，原样留
                continue

            new_value = _fix_value(
                raw_value=values[rec.id],
                prev_value=prev_value if prev_value is not None else (v0 if v0 is not None else values[rec.id]),
                v0=v0, skipped_days=row.skipped_days,
                rate_limit=rate_limit, delta_limit=delta_limit, cum_limit=cum_limit,
                # 按**这一条读数**的精度取整：读数记到整 mm 就给整数步（现场就是 1mm 精度）
                integral=float(rec.value_mm).is_integer(),
            )
            if abs(new_value - (values[rec.id] or 0.0)) > 1e-9:
                values[rec.id] = new_value
                planned[rec.id] = {"new_value_mm": new_value, "level": level,
                                   "old_cumulative_mm": row.cumulative_mm,
                                   "old_rate_mm_per_day": row.rate_mm_per_day}
                pending += 1
            prev_value = values[rec.id]
        if pending == 0:
            converged = True
            break

    # ── 收尾：以**修正后的最终序列**为准，分类成"已修好 / 改不动" ──
    fixes: list[dict] = []
    unfixable: list[dict] = []
    for rec, row in snapshot():
        if rec.value_mm is None or row.is_missing:
            continue
        new_value = values[rec.id]
        changed = abs((new_value or 0.0) - rec.value_mm) > 1e-9
        level_now = evaluate(row, limits)["level"]
        still_bad = level_now != "normal" and (include_control or level_now == "alarm")
        if changed and not still_bad:
            fixes.append({
                "point_no": point.point_no, "point_id": point.id, "series_type": series_type,
                "component": component, "obs_date": rec.obs_date.isoformat(),
                "record_id": rec.id, "old_value_mm": rec.value_mm, "new_value_mm": new_value,
                "level": planned.get(rec.id, {}).get("level", "control"),
                "old_cumulative_mm": planned.get(rec.id, {}).get("old_cumulative_mm"),
                "old_rate_mm_per_day": planned.get(rec.id, {}).get("old_rate_mm_per_day"),
                "new_cumulative_mm": row.cumulative_mm, "new_rate_mm_per_day": row.rate_mm_per_day,
            })
        elif still_bad:
            if rate_limit is None and cum_limit is None:
                reason = "该项目没配控制值/报警值，无法判断「安全值」"
            elif not converged:
                reason = "序列反复调整仍无法收敛（读数精度或限值过紧），建议人工核对"
            else:
                reason = "受单期变化上限约束，无法在保持读数精度的前提下修回限值以内"
            unfixable.append({
                "point_no": point.point_no, "series_type": series_type, "component": component,
                "obs_date": rec.obs_date.isoformat(), "old_value_mm": rec.value_mm,
                "level": level_now, "old_cumulative_mm": row.cumulative_mm,
                "old_rate_mm_per_day": row.rate_mm_per_day, "reason": reason,
            })

    return fixes, unfixable


def _fmt_mm(value: float) -> str:
    """读数写成固定小数（去掉多余的 0），**绝不用科学计数法**。

    踩过的坑：原先用 `{:g}`，位移坐标那种大数会写成 `5.10024e+08`，
    于是一条更正记录的备注长这样 ——「5.10024e+08 → 5.10024e+08 mm」，
    改前改后看起来一模一样，用户根本没法判断改了什么（实测生产库里 189 条都这样）。
    """
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _collect(db: Session, project_id: int, include_control: bool) -> tuple[list[dict], list[dict], int]:
    """扫全项目，返回 (可修正清单, 改不动的清单, 扫过的序列数)。只读。"""
    fixes: list[dict] = []
    unfixable: list[dict] = []
    series_count = 0

    for point in list_points(db, project_id, include_inactive=False):
        if not point.point_type:
            continue
        series_types = ["settlement", "displacement"] if point.point_type == "both" else [point.point_type]
        for st in series_types:
            limits = _limits_for(db, project_id, st)
            for comp in (["value"] if st == "settlement" else ["X", "Y"]):
                f, u = _scan_series(db, point, st, comp, limits, include_control)
                series_count += 1
                fixes += f
                unfixable += u
    return fixes, unfixable, series_count


def _summarize(project_id: int, fixes: list[dict], unfixable: list[dict], series_count: int) -> dict:
    return {
        "project_id": project_id,
        "series_scanned": series_count,
        "fixable": len(fixes),
        "unfixable": len(unfixable),
        "by_level": {
            "alarm": sum(1 for x in fixes if x["level"] == "alarm"),
            "control": sum(1 for x in fixes if x["level"] == "control"),
        },
        "samples": fixes[:_MAX_SAMPLES],
        "unfixable_samples": unfixable[:5],
    }


def plan_fixes(db: Session, project_id: int, include_control: bool = True) -> dict:
    """只读预检：算出将要修正哪些读数。**不落库**（dry_run）。"""
    fixes, unfixable, n = _collect(db, project_id, include_control)
    return {**_summarize(project_id, fixes, unfixable, n), "changed": 0, "dry_run": True}


def apply_fixes(db: Session, project_id: int, include_control: bool = True) -> dict:
    """执行修正：逐条走 `correct()`（旧值 superseded 保留、新值 source='correct'）。

    改完由调用方重建预警事件（派生表），保证页面上的等级立刻跟上。
    """
    fixes, unfixable, n = _collect(db, project_id, include_control)

    changed = 0
    for f in fixes:
        rec = observation_repo.get(db, f["record_id"])
        if rec is None or rec.value_mm is None:
            continue  # 期间被人改掉了，跳过而不是报错
        reason = "变化速率达到控制值" if f["level"] == "control" else "达到报警值"
        observation_repo.correct(
            db, rec, f["new_value_mm"],
            remark=(f"一键修正报警值：{_fmt_mm(f['old_value_mm'])} → {_fmt_mm(f['new_value_mm'])} mm"
                    f"（原{reason}，已修回限值以内；原值保留可追溯）"),
        )
        changed += 1

    return {**_summarize(project_id, fixes, unfixable, n), "changed": changed, "dry_run": False}
