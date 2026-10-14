# -*- coding: utf-8 -*-
"""初始值（V0）独立处理服务。

规则（用户确认）：
- 原始数据只在首次录入时计入一次，后续各期不再改动。
- V0 来源：测点显式 initial_value_mm 优先；否则 = 首期有效读数（first_observation）。
- 后续各期（第二期起）累计变化 C = Vn − V0，均以该原始数据为基准。
- 日期表标记：返回全部观测日期 + 初始值日期集合（供前端日历打点）。
"""
from sqlalchemy.orm import Session

from backend.repositories import observation_repo, point_repo
from backend.services.project_service import get_project


def project_baselines(db: Session, project_id: int) -> list[dict]:
    """每个测点各分量的初始值（V0）信息。"""
    get_project(db, project_id)
    points = point_repo.list_by_project(db, project_id, include_inactive=False)

    out = []
    for p in points:
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            for comp in comps:
                records = observation_repo.list_by_point(db, p.id, series_type=st, active_only=True)
                records = [r for r in records if r.component == comp]
                records.sort(key=lambda r: r.obs_date)
                first_valid = next((r for r in records if r.value_mm is not None), None)

                if p.initial_value_mm is not None:
                    # 显式初始值优先
                    out.append(
                        {
                            "point_id": p.id,
                            "point_no": p.point_no,
                            "series_type": st,
                            "component": comp,
                            "baseline_value_mm": p.initial_value_mm,
                            "baseline_date": first_valid.obs_date.isoformat() if first_valid else None,
                            "source": "explicit",
                            "note": "测点显式初始值",
                        }
                    )
                elif first_valid is not None:
                    # 自动取首期有效读数作为基准
                    out.append(
                        {
                            "point_id": p.id,
                            "point_no": p.point_no,
                            "series_type": st,
                            "component": comp,
                            "baseline_value_mm": first_valid.value_mm,
                            "baseline_date": first_valid.obs_date.isoformat(),
                            "source": "first_observation",
                            "note": "首期有效读数（自动基准）",
                        }
                    )
                # 无任何有效数据：不输出
    return out


def project_record_dates(db: Session, project_id: int) -> dict:
    """观测日历打点数据：
    - dates / baseline_dates：全部观测日期、初始值日期
    - dates_with_values：**有实际读数**的日期（缺测日不算）
    - alarm_dates / control_dates：发生报警 / 预警的日期（供日期表着色区分）

    ⚠ dates 与 dates_with_values 的区别（别合并成一个）：
      · `dates` 含**缺测日**（那天录了记录，只是值为空）—— 记录页的"已录入"横条该包含它，
        因为那天确实录过；
      · `dates_with_values` 只算有读数的日子 —— **导出日报必须用这个**：
        缺测日导出来是一张空表，用户以为有数据、点进去却是空的（生产库里实测就有 4 天这样，
        其中一天正是当天）。
    两个页面语义不同，所以两个字段都给，由调用方按场景选。

    ⚠ alarm 与 control **各自独立收集**，互不覆盖。
    早先的实现按「当天最坏等级」只归一类，结果是同一天既有报警又有预警时，
    预警日会被报警日吃掉 —— 日期表图例写着"红=报警日/金=预警日"，
    用户却查不到那天的预警，属于"预警定位不到预警日期"。
    同时发生时的显示优先级交给前端（报警优先），后端不丢信息。
    """
    get_project(db, project_id)
    baselines = project_baselines(db, project_id)
    baseline_dates = sorted({b["baseline_date"] for b in baselines if b["baseline_date"]})

    points = point_repo.list_by_project(db, project_id, include_inactive=False)
    dates: set[str] = set()
    value_dates: set[str] = set()
    alarm_dates: set[str] = set()
    control_dates: set[str] = set()

    from backend.services.calc_service import compute_point_series

    for p in points:
        records_all = observation_repo.list_by_point(db, p.id, active_only=True)
        for r in records_all:
            dates.add(r.obs_date.isoformat())
            if r.value_mm is not None:
                value_dates.add(r.obs_date.isoformat())
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            for comp in comps:
                series = compute_point_series(db, p, st, comp)
                for row in series:
                    if row["is_missing"]:
                        continue
                    d = row["obs_date"]
                    if row["warning_level"] == "alarm":
                        alarm_dates.add(d)
                    elif row["warning_level"] == "control":
                        control_dates.add(d)

    return {
        "dates": sorted(dates),
        "dates_with_values": sorted(value_dates),
        "baseline_dates": baseline_dates,
        "alarm_dates": sorted(alarm_dates),
        "control_dates": sorted(control_dates),
    }
