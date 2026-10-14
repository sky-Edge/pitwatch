# -*- coding: utf-8 -*-
"""项目速览服务 —— 给项目列表卡片提供「一句人话结论」。

动机：现场人员打开系统最想知道的是"现在怎么样、要不要管"，而不是一张数字表。
本服务把已经算好的结果压成一句话，例如：

    位移X JJC3 本期向基坑内 2.1mm 累计 12.4mm（控制值 20mm）

数据来源全部是已有能力，不新增任何工程公式：
- 计算/预警：calc_service.compute_project_warning_status（复用 Calculation/Warning Engine）；
- 方向语义：comparison_service._direction（按项目 sign_config 判定下沉/隆起、向内/向外）。

设计取舍：
- 只取「最该关注」的一个分量作为主结论（先按预警等级，再按控制值占用率），
  避免一句话里堆 20 个测点让用户读不完。
- 该结论是**附加信息**：任何一步失败都不应阻塞项目列表，调用方需容错。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.repositories import observation_repo, point_repo, project_repo
from backend.services import calc_service
from backend.services.comparison_service import _direction
from backend.services.project_service import get_project
from backend.services.warning_service import effective_rules

# 展示用语（避免页面上出现 settlement/displacement 这类内部字段名）
_SERIES_LABEL = {"settlement": "沉降", "displacement": "位移"}
_LEVEL_RANK = {"alarm": 0, "control": 1, "normal": 2}


def _compact_point_no(point_no: str) -> str:
    """过长编号截断，避免撑破卡片。"""
    return point_no if len(point_no) <= 12 else f"{point_no[:12]}…"


def _limit_mm(rules: dict, series_type: str) -> float | None:
    """该指标「控制级」累计限值，用于算占用率与文案；无规则返回 None。"""
    rule = rules.get(series_type, {}).get("control")
    return rule.threshold_cumulative_mm if rule else None


def _ratio(cumulative_mm: float | None, limit_mm: float | None) -> float:
    """控制值占用率，用于挑选"最该关注"的分量。无控制值时按 0 处理。"""
    if cumulative_mm is None or not limit_mm:
        return 0.0
    return abs(cumulative_mm) / limit_mm


def project_headline(db: Session, project_id: int) -> dict:
    """返回项目速览：一句结论 + 最关注的分量明细。

    headline 为 None 表示该项目还没有可用数据（尚未录入 / 只有初始值）。
    """
    project = get_project(db, project_id)
    sign_config = project.sign_config or {}

    rows = calc_service.compute_project_warning_status(db, project_id)
    rows = [r for r in rows if r.get("cumulative_mm") is not None]
    if not rows:
        return {
            "project_id": project_id,
            "headline": None,
            "severity": "none",
            "obs_date": None,
            "detail": None,
            "counts": {"alarm": 0, "control": 0, "normal": 0},
        }

    counts = {
        "alarm": len([r for r in rows if r["warning_level"] == "alarm"]),
        "control": len([r for r in rows if r["warning_level"] == "control"]),
        "normal": len([r for r in rows if r["warning_level"] == "normal"]),
    }

    rules = effective_rules(db, project_id)

    # 先按预警等级，再按控制值占用率排序 —— 报警项优先被选为结论
    rows.sort(
        key=lambda r: (
            _LEVEL_RANK.get(r["warning_level"], 3),
            -_ratio(r["cumulative_mm"], _limit_mm(rules, r["series_type"])),
        )
    )

    top = rows[0]
    st = top["series_type"]
    dr = _direction(sign_config, st, top.get("delta_mm"))
    label = _SERIES_LABEL.get(st, st)
    comp = "" if top["component"] == "value" else top["component"]

    # 文案要点：变化量与累计量都带方向，且都取绝对值配方向词。
    # 直接用绝对值会让「本期 7.0mm / 累计 6.0mm」看起来自相矛盾
    # （实测确实存在回弹后再下沉的情况），所以两个数都各自标方向。
    parts = [f"{label}{comp} {_compact_point_no(top['point_no'])}"]
    delta = top.get("delta_mm")
    if delta is not None and dr["direction"] != "stable":
        parts.append(f"本期{dr['direction_label']}{abs(delta):.1f}mm")
    cum = top.get("cumulative_mm")
    if cum is not None and abs(cum) > 1e-9:
        cum_dr = _direction(sign_config, st, cum)
        limit = _limit_mm(rules, st)
        cum_txt = f"累计{cum_dr['direction_label']}{abs(cum):.1f}mm"
        parts.append(f"{cum_txt}（控制值 {limit:.0f}mm）" if limit else cum_txt)

    # 只有初始值、还没有变化量的项目不给结论（一句"累计0.0mm"没有信息量）
    if len(parts) <= 1:
        return {
            "project_id": project_id,
            "headline": None,
            "severity": "none",
            "obs_date": None,
            "detail": None,
            "counts": counts,
        }

    return {
        "project_id": project_id,
        "headline": " ".join(parts),
        "severity": top["warning_level"],
        "obs_date": top.get("obs_date"),
        "detail": top,
        "counts": counts,
        "points_with_data": len({r["point_no"] for r in rows}),
    }


def all_project_headlines(db: Session) -> list[dict]:
    """全部项目速览（项目列表页一次取回）。单项失败不影响其余。"""
    out: list[dict] = []
    for p in project_repo.list_all(db, include_inactive=True):
        try:
            out.append(project_headline(db, p.id))
        except Exception:  # noqa: BLE001 —— 速览是附加信息，不应拖垮列表
            out.append(
                {
                    "project_id": p.id,
                    "headline": None,
                    "severity": "none",
                    "obs_date": None,
                    "detail": None,
                    "counts": {"alarm": 0, "control": 0, "normal": 0},
                }
            )
    return out


def last_obs_info(db: Session, project_id: int) -> dict:
    """最近观测日期与观测期数（供"多久没测了"提示）。"""
    points = point_repo.list_by_project(db, project_id, include_inactive=False)
    dates: set = set()
    for p in points:
        dates.update(r.obs_date for r in observation_repo.list_by_point(db, p.id, active_only=True))
    if not dates:
        return {"last_obs_date": None, "obs_days": 0}
    return {"last_obs_date": max(dates).isoformat(), "obs_days": len(dates)}
