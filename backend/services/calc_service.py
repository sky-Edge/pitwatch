# -*- coding: utf-8 -*-
"""Calculation + Warning 查询服务。

实时计算（不落库）：读取生效记录 → Calculation Engine → Warning Engine。
calculation_results 持久化表在 Phase 4 落库环节再引入；本层输出纯函数结果。
"""
from datetime import date

from sqlalchemy.orm import Session

from backend.calculation.engine import PeriodInput, compute_series, latest_valid_result
from backend.models.observation_record import ObservationRecord
from backend.models.point import MeasurementPoint
from backend.repositories import observation_repo
from backend.services.point_service import get_point
from backend.services.warning_service import effective_rules
from backend.warnings.engine import RuleLimits, evaluate


def _record_to_period(r: ObservationRecord) -> PeriodInput:
    return PeriodInput(obs_date=r.obs_date, value_mm=r.value_mm, status=r.status)


def compute_point_series(db: Session, point: MeasurementPoint, series_type: str, component: str) -> list[dict]:
    """某测点某分量的计算序列（含每期预警等级）。"""
    records = observation_repo.list_by_point(db, point.id, series_type=series_type, active_only=True)
    records = [r for r in records if r.component == component]
    periods = [_record_to_period(r) for r in records]
    results = compute_series(periods, initial_value_mm=point.initial_value_mm)

    limits_map = effective_rules(db, point.project_id).get(series_type, {})
    limits = RuleLimits(
        control_cumulative_mm=limits_map.get("control").threshold_cumulative_mm if limits_map.get("control") else None,
        control_rate_mm_per_day=limits_map.get("control").threshold_rate_mm_per_day if limits_map.get("control") else None,
        alarm_cumulative_mm=limits_map.get("alarm").threshold_cumulative_mm if limits_map.get("alarm") else None,
        alarm_rate_mm_per_day=limits_map.get("alarm").threshold_rate_mm_per_day if limits_map.get("alarm") else None,
    )

    out = []
    value_by_date = {r.obs_date: r.value_mm for r in records}
    for r in results:
        ev = evaluate(r, limits) if not r.is_missing else {"level": "normal", "triggers": []}
        row = r.to_dict()
        row["value_mm"] = value_by_date.get(r.obs_date)
        row["warning_level"] = ev["level"]
        row["triggers"] = ev["triggers"]
        out.append(row)
    return out


def compute_project_warning_status(db: Session, project_id: int, obs_date: str | None = None) -> list[dict]:
    """项目所有测点的预警状态总览。

    `obs_date=None`（默认）：取每项**最新一期**——用于项目列表的"当前状态"。
    `obs_date="YYYY-MM-DD"`：取**该观测日**的状态——用于观测记录页：
    用户定位到某一天时，表格里的预警标记必须反映那一天的读数，
    否则会出现"定位到 09-03，却显示 09-15 的预警等级"这种前后矛盾。
    该日无有效数据时按正常返回，并把 obs_date 置为 None。
    """
    from backend.repositories import point_repo

    points = point_repo.list_by_project(db, project_id, include_inactive=False)
    status: list[dict] = []
    for p in points:
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            for comp in comps:
                series = compute_point_series(db, p, st, comp)
                if obs_date is None:
                    picked = latest_valid_result_for(series)
                else:
                    picked = next(
                        (r for r in series if r["obs_date"] == obs_date and not r["is_missing"]),
                        None,
                    )
                status.append(
                    {
                        "point_id": p.id,
                        "point_no": p.point_no,
                        "series_type": st,
                        "component": comp,
                        "obs_date": picked["obs_date"] if picked else None,
                        "value_mm": picked["value_mm"] if picked else None,
                        "delta_mm": picked["delta_mm"] if picked else None,
                        "cumulative_mm": picked["cumulative_mm"] if picked else None,
                        "rate_mm_per_day": picked["rate_mm_per_day"] if picked else None,
                        "warning_level": picked["warning_level"] if picked else "normal",
                        "triggers": picked["triggers"] if picked else [],
                    }
                )
    return status


def latest_valid_result_for(series: list[dict]) -> dict | None:
    for row in reversed(series):
        if not row["is_missing"]:
            return row
    return None


def project_warning_summary(db: Session, project_id: int) -> dict:
    """单项目预警汇总。"""
    rows = compute_project_warning_status(db, project_id)
    alarm = [r for r in rows if r["warning_level"] == "alarm"]
    control = [r for r in rows if r["warning_level"] == "control"]
    return {
        "project_id": project_id,
        "has_alarm": bool(alarm),
        "has_control": bool(control),
        "alarm_points": alarm,
        "control_points": control,
        "total_points": len(rows),
    }


def all_projects_warning_summary(db: Session) -> list[dict]:
    """所有项目预警汇总（项目列表页用，一次取回）。"""
    from backend.repositories import project_repo

    projects = project_repo.list_all(db, include_inactive=True)
    out = []
    for p in projects:
        summary = project_warning_summary(db, p.id)
        summary["project_code"] = p.code
        summary["project_name"] = p.name
        out.append(summary)
    return out
