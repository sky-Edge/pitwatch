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
from backend.services.warning_service import rule_limits
from backend.warnings.engine import evaluate


def _record_to_period(r: ObservationRecord) -> PeriodInput:
    return PeriodInput(obs_date=r.obs_date, value_mm=r.value_mm, status=r.status)


def _round2(v: float | None) -> float | None:
    """mm 数值收到 2 位（0.01mm），顺手抹掉浮点尾巴。

    `-7.200000286102295` 这种数直接印在首页，用户第一反应是"这系统算错了"，
    实际只是二进制浮点表示误差。工程的读数精度到 0.01mm 已经足够。
    """
    if v is None:
        return None
    return round(float(v), 2)


def compute_point_series(
    db: Session,
    point: MeasurementPoint,
    series_type: str,
    component: str,
    *,
    records: list[ObservationRecord] | None = None,
    limits=None,
) -> list[dict]:
    """某测点某分量的计算序列（含每期预警等级）。

    `records` / `limits` 是**可选**的预取参数：批量场景（预警汇总、项目结论、
    关注项）里，调用方一次性把整个项目的记录和该项目的规则查好再传进来，
    避免"每个测点各查一次"。不传时保持原来的单点查询行为，其它调用方无需改动。
    """
    if records is None:
        records = observation_repo.list_by_point(db, point.id, series_type=series_type, active_only=True)
    records = [r for r in records if r.series_type == series_type and r.component == component]
    periods = [_record_to_period(r) for r in records]
    if limits is None:
        limits = rule_limits(db, point.project_id, series_type)
    # 禁止回弹写在规则里（项目 → 规则套 → 该指标 control/alarm 任一级勾了即生效）。
    # 只在沉降上勾有意义：水平位移有来有回是真实情况，抹平就等于抹掉变形。
    results = compute_series(
        periods,
        initial_value_mm=point.initial_value_mm,
        no_rebound=limits.no_rebound and series_type == "settlement",
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
    if not points:
        return []

    # ⚡ 一次取回整个项目的生效记录，再按测点分组。
    # 原来是对每个测点的每个分量各查一次（62 点 → 154 次查询），
    # 实测这是 `/warning-summary` 第二大开销（276ms）。
    by_point = observation_repo.group_by_point(
        observation_repo.list_by_project(db, project_id, active_only=True)
    )
    # ⚡ 规则也按"项目 × 指标"缓存：`rule_limits` 内部要翻规则套/通用套/老式行，
    # 一次调用约 6 条 SQL。原来每个分量调一次（154 次 ≈ 900 条 SQL），占了一半耗时。
    limits_cache: dict[str, object] = {}

    status: list[dict] = []
    for p in points:
        point_records = by_point.get(p.id, [])
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            if st not in limits_cache:
                limits_cache[st] = rule_limits(db, project_id, st)
            limits = limits_cache[st]
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            for comp in comps:
                series = compute_point_series(
                    db, p, st, comp, records=point_records, limits=limits
                )
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
                        # 数值一律取到 2 位小数（0.01mm）。
                        # 为什么在这里收口：计算引擎里的浮点误差会原样带到界面 ——
                        # 实测出现过「累计 -7.200000286102295mm」这种数字直接印在首页，
                        # 一眼就不像工程资料。汇总接口的消费方（首页横幅、详情表、项目行）
                        # 都从这一个 dict 取数，在这里 round 一次，全部干净。
                        "value_mm": _round2(picked["value_mm"]) if picked else None,
                        "delta_mm": _round2(picked["delta_mm"]) if picked else None,
                        "cumulative_mm": _round2(picked["cumulative_mm"]) if picked else None,
                        "rate_mm_per_day": _round2(picked["rate_mm_per_day"]) if picked else None,
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
        # 归档项目**仍要算出状态**（列表切到「全部」时该显示的还是要有），
        # 但前端据此把"报警提示"过滤掉 —— 归档 = 工点结束，不该每天弹它
        # （用户 2026-11-16：归档的项目不要再报警提示了）。
        summary["is_active"] = bool(p.is_active)
        out.append(summary)
    return out
