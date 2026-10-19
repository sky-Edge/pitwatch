# -*- coding: utf-8 -*-
"""测点平面布置：把一个基坑里的测点摆到一张图上，看它们的位置关系。

用户 2026-10-19：「我想看到我每个基坑中初始值在 CAD 中的位置关系」

**坐标从哪来** —— 测点表里没有存平面坐标，但**水平位移的 X/Y 首期读数就是大地坐标**：
五号右边墩 JJC1 的 `3412987154 / 510224375`（mm）就是 `X 3412987.154 m / Y 510224.375 m`。
所以取每个点**最早观测日**的 X、Y 当作它的初始位置。纯沉降点只有高程、没有平面位置，
画不上图，但必须在结果里**点名**（绝不静默少几个点）。

**为什么画图前必须做离群检测** —— 实测九号基坑的数据里，Y 值被导进了 X 分量
（`X=510146207`，量级就是 Y）。这种点混进来会把包围盒撑到几百公里，
整张图缩成一个点、其他点全糊在中间，用户看到的是"图坏了"，而不是"某个点录错了"。
判据用**中位数 + 50 公里**：基坑尺度几十米，同一次测点之间不该差出 50 公里；
这个阈值松到不会误杀跨基坑联动（几公里），又能抓住"Y 混进 X"那种差 3×10⁶ m 的错。
"""
from __future__ import annotations

import re
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.observation_record import ObservationRecord
from backend.models.point import MeasurementPoint, SERIES_TYPES
from backend.repositories.observation_repo import INACTIVE_STATUS
from backend.services import point_service

#: 离群判据（mm）。见模块头注释。
OUTLIER_MM = 50_000_000.0

#: 点号自然排序：JJC2 要排在 JJC10 前面（直接按字符串排会反）。
_NUM_RE = re.compile(r"(\d+)")


def _natural_key(point_no: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in _NUM_RE.split(point_no or "")]


def _m(mm: float) -> float:
    """mm → m，保留 3 位（= 1mm 精度，和台账一致）。"""
    return round(mm / 1000.0, 3)


def _first_xy(db: Session, project_id: int) -> dict[int, dict[str, float]]:
    """每个测点**最早观测日**的 X、Y（mm）。

    按 (点, 分量, 日期) 升序取第一次出现的值 —— 也就是"初始位置"。
    作废/被覆盖的记录必须排除（与全系统同一条口径）。
    """
    stmt = (
        select(
            ObservationRecord.point_id,
            ObservationRecord.component,
            ObservationRecord.value_mm,
            ObservationRecord.obs_date,
        )
        .join(MeasurementPoint, MeasurementPoint.id == ObservationRecord.point_id)
        .where(MeasurementPoint.project_id == project_id)
        .where(ObservationRecord.component.in_(("X", "Y")))
        .where(ObservationRecord.value_mm.is_not(None))
        .where(ObservationRecord.status.not_in(list(INACTIVE_STATUS)))
        .order_by(
            ObservationRecord.point_id,
            ObservationRecord.component,
            ObservationRecord.obs_date,
        )
    )
    out: dict[int, dict[str, float]] = {}
    for point_id, comp, value, _date in db.execute(stmt):
        out.setdefault(point_id, {}).setdefault(comp, float(value))
    return out


def build_layout(db: Session, project_id: int) -> dict:
    """返回这个项目的测点平面布置（坐标单位 m）。"""
    points = point_service.list_points(db, project_id, include_inactive=False)
    xy = _first_xy(db, project_id)

    placed: list[dict] = []
    missing: list[dict] = []
    pending: list[tuple] = []  # (point, x_mm, y_mm) —— 先收齐再做离群判断

    for p in points:
        got = xy.get(p.id) or {}
        x, y = got.get("X"), got.get("Y")
        if x is None and y is None:
            why = (
                "这个测点只做沉降观测，没有平面坐标"
                if SERIES_TYPES.get(p.point_type, ()) == ("settlement",)
                else "还没有位移读数，取不到初始位置"
            )
            missing.append({"point_no": p.point_no, "why": why})
            continue
        if x is None or y is None:
            miss = "X" if x is None else "Y"
            missing.append({"point_no": p.point_no, "why": f"缺 {miss} 坐标（只有半个点，画不出来）"})
            continue
        pending.append((p, float(x), float(y)))

    # ── 离群检测：先看全体中位数，再把离谱的点摘出去 ──
    if pending:
        med_x = median(t[1] for t in pending)
        med_y = median(t[2] for t in pending)
    else:
        med_x = med_y = 0.0

    for p, x, y in pending:
        if abs(x - med_x) > OUTLIER_MM or abs(y - med_y) > OUTLIER_MM:
            missing.append(
                {
                    "point_no": p.point_no,
                    "why": (
                        f"坐标离群（X {_m(x):,.3f} / Y {_m(y):,.3f} m），"
                        "多半是导入时 X/Y 弄反了"
                    ),
                }
            )
            continue
        placed.append(
            {
                "point_no": p.point_no,
                "point_type": p.point_type,
                "x_m": _m(x),
                "y_m": _m(y),
            }
        )

    placed.sort(key=lambda d: _natural_key(d["point_no"]))

    # ── 相邻点间距：按点号顺序连（现场布点通常就是沿基坑周边这么编的）──
    segments = []
    for a, b in zip(placed, placed[1:]):
        dist = ((a["x_m"] - b["x_m"]) ** 2 + (a["y_m"] - b["y_m"]) ** 2) ** 0.5
        segments.append({"from": a["point_no"], "to": b["point_no"], "length_m": round(dist, 2)})

    bbox = None
    if placed:
        xs = [p["x_m"] for p in placed]
        ys = [p["y_m"] for p in placed]
        bbox = {
            "min_x_m": min(xs),
            "max_x_m": max(xs),
            "min_y_m": min(ys),
            "max_y_m": max(ys),
            "width_m": round(max(xs) - min(xs), 2),
            "height_m": round(max(ys) - min(ys), 2),
        }

    return {
        "project_id": project_id,
        "points": placed,
        "missing": missing,
        "segments": segments,
        "bbox": bbox,
        # 有坐标的点太少时画出来只是一堆散点，界面上要据此换措辞
        "placed_count": len(placed),
        "missing_count": len(missing),
    }
