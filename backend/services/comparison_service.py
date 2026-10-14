# -*- coding: utf-8 -*-
"""逐期对比分析服务 —— 报告引擎（Phase 8）的基础数据源。

对比规则（各期持续沿用）：
- 每期与"上一期"对比；首期无上一期则与初始值期对比并标记 kind=first；
- 中间缺测时与最近有效期对比，kind=skip 并标注 skipped_days；
- 关注维度：本次变化 ΔV、累计变化 C、速率 Rate、变化方向、预警状态、跨期标记；
- 方向语义来自项目 sign_config（positive_means），未配置时按行业惯例：
  沉降正值=下沉；水平位移正值=向基坑内。
"""
from datetime import date

from sqlalchemy.orm import Session

from backend.calculation.engine import PeriodInput, compute_series
from backend.models.observation_record import ObservationRecord
from backend.models.point import MeasurementPoint
from backend.repositories import observation_repo
from backend.services.warning_service import effective_rules
from backend.warnings.engine import RuleLimits, evaluate


def _direction(sign_config: dict | None, series_type: str, delta_mm: float | None) -> dict:
    """依据项目正负号规则判断变化方向。"""
    if delta_mm is None or abs(delta_mm) < 1e-9:
        return {"direction": "stable", "direction_label": "无变化"}
    cfg = (sign_config or {}).get(series_type, {})
    if series_type == "settlement":
        positive_means = cfg.get("positive_means", "down")
        down = delta_mm > 0 if positive_means == "down" else delta_mm < 0
        label = "下沉" if down else "隆起"
        return {"direction": "down" if down else "up", "direction_label": label}
    # displacement
    positive_means = cfg.get("positive_means", "toward_pit")
    toward = delta_mm > 0 if positive_means == "toward_pit" else delta_mm < 0
    label = "向基坑内" if toward else "向基坑外"
    return {"direction": "toward_pit" if toward else "away", "direction_label": label}


def compare_point_series(
    db: Session,
    point: MeasurementPoint,
    series_type: str,
    component: str,
    sign_config: dict | None = None,
    global_dates: list[date] | None = None,
) -> dict:
    """单测点单分量的逐期对比序列。

    global_dates：项目全部观测日（含初始值日，升序）。传参时"期次"按全局
    观测序号编号（0=初始值日 → 初始值，其后 第一期/第二期…），与导出口径一致；
    缺省时退化为序列内部 1 起始序号（旧行为）。
    """
    records = observation_repo.list_by_point(db, point.id, series_type=series_type, active_only=True)
    records = [r for r in records if r.component == component]
    records.sort(key=lambda r: r.obs_date)

    periods = [
        PeriodInput(obs_date=r.obs_date, value_mm=r.value_mm, status=r.status) for r in records
    ]
    results = compute_series(periods, initial_value_mm=point.initial_value_mm)
    value_by_date: dict[date, float | None] = {r.obs_date: r.value_mm for r in records}

    limits_map = effective_rules(db, point.project_id).get(series_type, {})
    limits = RuleLimits(
        control_cumulative_mm=limits_map.get("control").threshold_cumulative_mm if limits_map.get("control") else None,
        control_rate_mm_per_day=limits_map.get("control").threshold_rate_mm_per_day if limits_map.get("control") else None,
        alarm_cumulative_mm=limits_map.get("alarm").threshold_cumulative_mm if limits_map.get("alarm") else None,
        alarm_rate_mm_per_day=limits_map.get("alarm").threshold_rate_mm_per_day if limits_map.get("alarm") else None,
    )

    def _period_no(obs: date, idx: int) -> int:
        """期次 = 全局观测日序号（0=初始值日）；日期不在全局列表时退回序列内序号。"""
        if global_dates is not None:
            try:
                return global_dates.index(obs)
            except ValueError:
                return idx
        return idx + 1

    rows = []
    prev_valid: date | None = None
    for idx, r in enumerate(results):
        if r.is_missing:
            rows.append(
                {
                    "period": _period_no(r.obs_date, idx),
                    "obs_date": r.obs_date.isoformat(),
                    "compare_to_date": r.prev_valid_date.isoformat() if r.prev_valid_date else None,
                    "compare_kind": "missing",
                    "skipped_days": None,
                    "value_mm": value_by_date.get(r.obs_date),
                    "prev_value_mm": value_by_date.get(r.prev_valid_date) if r.prev_valid_date else None,
                    "delta_mm": None,
                    "cumulative_mm": None,
                    "rate_mm_per_day": None,
                    "direction": "missing",
                    "direction_label": "缺测",
                    "warning_level": "normal",
                    "triggers": [],
                }
            )
            continue

        # 对比基准与类型
        if prev_valid is None:
            kind = "first"  # 首期（相对初始值期）
            compare_to = None
        elif r.prev_valid_date == prev_valid and (r.obs_date - prev_valid).days == 1:
            kind = "adjacent"  # 紧邻上期
            compare_to = prev_valid
        else:
            kind = "skip"  # 跨期（中间缺测）
            compare_to = r.prev_valid_date

        ev = evaluate(r, limits)
        dr = _direction(sign_config, series_type, r.delta_mm)
        rows.append(
            {
                "period": _period_no(r.obs_date, idx),
                "obs_date": r.obs_date.isoformat(),
                "compare_to_date": compare_to.isoformat() if compare_to else None,
                "compare_kind": kind,
                "skipped_days": r.skipped_days,
                "value_mm": value_by_date.get(r.obs_date),
                "prev_value_mm": value_by_date.get(r.prev_valid_date) if r.prev_valid_date else None,
                "delta_mm": r.delta_mm,
                "cumulative_mm": r.cumulative_mm,
                "rate_mm_per_day": r.rate_mm_per_day,
                "direction": dr["direction"],
                "direction_label": dr["direction_label"],
                "warning_level": ev["level"],
                "triggers": ev["triggers"],
            }
        )
        prev_valid = r.obs_date

    return {
        "point_id": point.id,
        "point_no": point.point_no,
        "series_type": series_type,
        "component": component,
        "unit_note": "读值单位 m（入库 mm），变化量/累计/速率单位 mm、mm/d",
        "periods": rows,
    }


def project_comparison(db: Session, project_id: int, sign_config: dict | None = None) -> list[dict]:
    """项目所有测点全分量的逐期对比（报告用）。

    期次采用项目全局观测日序号（0=初始值日，其后第一期/第二期…），
    与母版/内置日报导出的期次口径保持一致——首个观测日不叫第一期而叫初始值。
    """
    from backend.repositories import point_repo
    from backend.services.project_service import get_project

    project = get_project(db, project_id)
    sign_config = sign_config if sign_config is not None else (project.sign_config or {})
    points = point_repo.list_by_project(db, project_id, include_inactive=False)

    # 项目全部观测日（含初始值日）升序 —— 全局期次基准
    global_dates = sorted({
        r.obs_date
        for p in points
        for r in observation_repo.list_by_point(db, p.id, active_only=True)
    })

    out = []
    for p in points:
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            for comp in comps:
                series = compare_point_series(db, p, st, comp, sign_config, global_dates)
                if series["periods"]:
                    out.append(series)
    return out
