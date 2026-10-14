# -*- coding: utf-8 -*-
"""Chart Data Builder —— 为 ECharts 曲线图提供结构化数据。

设计（对应 Phase 0 的 Chart Data Builder）：
- 一次取回项目内指定指标（沉降/位移）全测点的序列，前端直接喂给 ECharts；
- 输出含：x 轴日期（连续去重）、每测点每分量的累计变化序列、预警叠加点（报警/预警/正常着色）；
- 所有计算仍走 Calculation Engine（本层只做数据整形，零公式）。

接口：GET /projects/{pid}/chart-data?indicator=settlement&component=value
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.services import calc_service
from backend.services.point_service import list_points


def build_chart_data(db: Session, project_id: int, indicator: str, component: str | None = None) -> dict:
    """构建多测点曲线数据。

    indicator: settlement / displacement
    component: settlement→value；displacement→X/Y（缺省取 X）
    """
    points = list_points(db, project_id, include_inactive=False)
    series: list[dict] = []
    all_dates: set[str] = set()
    warning_points: list[dict] = []

    # 先给 comp 一个兜底值：如果项目里**一个匹配的测点都没有**（比如只有沉降点却查位移，
    # 或项目刚建还没建测点），下面的循环一次都不会执行，comp 就不会被赋值 →
    # 返回时 UnboundLocalError → 接口 500（实测：用户点开曲线图直接报错，日志里的堆栈）。
    comp = "value" if indicator == "settlement" else (component or "X")

    for p in points:
        if indicator == "settlement":
            if p.point_type not in ("settlement", "both"):
                continue
            comp = "value"
        else:  # displacement
            if p.point_type not in ("displacement", "both"):
                continue
            comp = component or "X"

        rows = calc_service.compute_point_series(db, p, indicator, comp)
        data: list[dict] = []
        for r in rows:
            if r["is_missing"]:
                continue  # 缺测点不落线（曲线不断开，视觉连贯）
            data.append({"date": r["obs_date"], "cumulative_mm": r["cumulative_mm"]})
            all_dates.add(r["obs_date"])
            # 预警叠加点：仅非正常等级
            if r["warning_level"] in ("control", "alarm"):
                warning_points.append(
                    {
                        "point_no": p.point_no,
                        "series_type": indicator,
                        "component": comp,
                        "date": r["obs_date"],
                        "cumulative_mm": r["cumulative_mm"],
                        "rate_mm_per_day": r["rate_mm_per_day"],
                        "warning_level": r["warning_level"],
                        "triggers": r["triggers"],
                    }
                )
        series.append(
            {
                "point_id": p.id,
                "point_no": p.point_no,
                "series_type": indicator,
                "component": comp,
                "data": data,
            }
        )

    dates = sorted(all_dates)
    return {
        "indicator": indicator,
        "component": comp,
        "dates": dates,
        "series": series,
        "warning_points": warning_points,
        "unit": "mm",
    }
