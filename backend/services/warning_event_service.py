# -*- coding: utf-8 -*-
"""Warning Engine 落库 + 数据质量检查服务。

职责：
1. recompute_project —— 重算项目全部测点：先清空该项目 warning_events，再按
   Calculation/Warning Engine 重新生成事件（recompute_batch 留痕）。幂等可重建。
2. detect_quality_issues —— 数据质量检查（只标记不删除）：
   - 突变检测：单期 |ΔV| 超过项目配置的突变阈值 → 建议标记 suspect；
   - 缺测提醒：缺测期统计（跨期天数标记已在计算引擎处理）。

设计约束：本服务只写派生表（warning_events），永不触碰 observation_records；
warning_events 随重算整体重建，不会重复堆积。
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.calculation.engine import PeriodInput, compute_series
from backend.models.observation_record import ObservationRecord
from backend.models.warning_event import WarningEvent
from backend.repositories import observation_repo, point_repo
from backend.services.point_service import list_points
from backend.services.warning_service import rule_limits
from backend.warnings.engine import RuleLimits, evaluate

# 突变阈值默认值（mm/day），项目可在 sign_config 或规则中调整 —— 配置化，不硬编码
DEFAULT_MUTATION_THRESHOLD_MM = 10.0


def _limits_for(db: Session, project_id: int, series_type: str) -> RuleLimits:
    """判定用的四个阈值 —— 取数只有 `warning_service.rule_limits` 一处。"""
    return rule_limits(db, project_id, series_type)


def _series_records(db: Session, point_id: int, series_type: str, component: str) -> list[ObservationRecord]:
    records = observation_repo.list_by_point(db, point_id, series_type=series_type, active_only=True)
    return [r for r in records if r.component == component]


def recompute_project(db: Session, project_id: int) -> dict:
    """重算项目全部测点，重建 warning_events。返回事件统计。"""
    batch = uuid.uuid4().hex[:12]

    # 先清空该项目派生事件（整体重建，幂等）
    db.execute(delete(WarningEvent).where(WarningEvent.project_id == project_id))
    db.flush()

    points = list_points(db, project_id, include_inactive=False)
    events: list[WarningEvent] = []
    point_names = {p.id: p.point_no for p in points}

    for p in points:
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            limits = _limits_for(db, project_id, st)
            for comp in comps:
                records = _series_records(db, p.id, st, comp)
                if not records:
                    continue
                periods = [PeriodInput(obs_date=r.obs_date, value_mm=r.value_mm, status=r.status) for r in records]
                results = compute_series(periods, initial_value_mm=p.initial_value_mm)

                value_by_date = {r.obs_date: r.value_mm for r in records}
                for r in results:
                    if r.is_missing:
                        continue
                    ev = evaluate(r, limits)
                    if ev["level"] == "normal":
                        continue
                    # 触发项拆分（同一期可能同时触发累计 + 本次变化 + 速率 → 多条事件）
                    for trigger_type, value, tcheck, label, unit in (
                        ("cumulative", r.cumulative_mm, ev["cumulative_check"], "累计变化", "mm"),
                        ("delta", r.delta_mm, ev["delta_check"], "本次变化", "mm"),
                        ("rate", r.rate_mm_per_day, ev["rate_check"], "变化速率", "mm/d"),
                    ):
                        if tcheck not in ("control", "alarm"):
                            continue
                        events.append(
                            WarningEvent(
                                project_id=project_id,
                                point_id=p.id,
                                series_type=st,
                                component=comp,
                                obs_date=r.obs_date,
                                indicator=st,
                                level=ev["level"],
                                trigger_type=trigger_type,
                                value_mm=value,
                                rate_mm_per_day=r.rate_mm_per_day,
                                message=(
                                    f"{point_names[p.id]} {st}/{comp} "
                                    f"{label} {value} {unit} 达到{'报警' if tcheck == 'alarm' else '控制'}值"
                                ),
                                recompute_batch=batch,
                            )
                        )
    db.add_all(events)
    db.commit()

    return {"recompute_batch": batch, "events_generated": len(events), "points": len(points)}


def detect_quality_issues(db: Session, project_id: int) -> dict:
    """数据质量检查：突变检测 + 缺测统计（只标记提示，不删除、不修改）。"""
    points = list_points(db, project_id, include_inactive=False)
    suspects: list[dict] = []
    missing: list[dict] = []
    mutation_threshold = _mutation_threshold(db, project_id)

    for p in points:
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            for comp in comps:
                records = _series_records(db, p.id, st, comp)
                if not records:
                    continue
                periods = [PeriodInput(obs_date=r.obs_date, value_mm=r.value_mm, status=r.status) for r in records]
                results = compute_series(periods, initial_value_mm=p.initial_value_mm)

                for r in results:
                    if r.is_missing:
                        missing.append(
                            {
                                "point_no": p.point_no,
                                "series_type": st,
                                "component": comp,
                                "obs_date": r.obs_date.isoformat(),
                                "note": "缺测",
                            }
                        )
                        continue
                    # 突变检测：单期 |ΔV| 超过阈值（且非缺测继承）
                    if r.delta_mm is not None and abs(r.delta_mm) > mutation_threshold:
                        suspects.append(
                            {
                                "point_no": p.point_no,
                                "series_type": st,
                                "component": comp,
                                "obs_date": r.obs_date.isoformat(),
                                "delta_mm": r.delta_mm,
                                "rate_mm_per_day": r.rate_mm_per_day,
                                "note": f"单期变化 {r.delta_mm:.1f}mm 超过突变阈值 {mutation_threshold:.0f}mm，请复核是否为抄录/测量错误",
                            }
                        )
    return {
        "project_id": project_id,
        "mutation_threshold_mm": mutation_threshold,
        "suspects": suspects,
        "missing": missing,
        "suspect_count": len(suspects),
        "missing_count": len(missing),
    }


def _mutation_threshold(db: Session, project_id: int) -> float:
    """突变阈值：项目 sign_config.data_quality.mutation_threshold_mm 可配置，缺省用默认值。"""
    from backend.models.project import Project

    project = db.get(Project, project_id)
    if project and project.sign_config:
        dq = project.sign_config.get("data_quality") or {}
        val = dq.get("mutation_threshold_mm")
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    return DEFAULT_MUTATION_THRESHOLD_MM
