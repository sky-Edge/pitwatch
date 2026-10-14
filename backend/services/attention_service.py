# -*- coding: utf-8 -*-
"""待办/关注汇总服务 —— 全部项目「需要我处理什么」的单一入口。

动机：系统里预警事件、数据质量、观测日期这三块信息分散在三个页面，
用户必须逐个项目点进去才知道有没有事。本服务把它们合成一张跨项目清单，
让首页一次说清楚：哪个项目报警了、哪条数据可疑、哪个项目很久没测了。

数据来源全部已有，不新增判定规则：
- 预警事件：warning_events（由 warning_event_service.recompute_project 重建）
- 数据质量：warning_event_service.detect_quality_issues（突变/缺测，只标记不删除）
- 观测日期：project_brief_service.last_obs_info

设计取舍：
- 只做"提示"，不做推送。系统定位是离线单机工具，不引入短信/邮件依赖。
- 每类关注项都带 project_id，前端可直接点进对应项目处理。
- 单项失败不影响其余（逐项目 try/except）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from backend.repositories import project_repo
from backend.services import project_brief_service, warning_event_service

# 超过这个天数没观测就算"该测了"（现场常规频率为 1~3 天一期，取 3 天兜底）
STALE_DAYS = 3
# 每个项目的质量明细最多返回多少条，避免清单过长
MAX_QUALITY_PER_PROJECT = 10


def _stale_days(last_obs_date: str | None, today: date) -> int | None:
    if not last_obs_date:
        return None
    try:
        d = date.fromisoformat(last_obs_date)
    except ValueError:
        return None
    return (today - d).days


def project_attention(db: Session, project_id: int, today: date | None = None) -> dict:
    """单项目的关注项汇总（报警 / 数据质量 / 是否该观测）。"""
    today = today or date.today()

    # 注意：此处只读取，不触发 recompute —— 列表页不应产生写库副作用。
    quality = warning_event_service.detect_quality_issues(db, project_id)
    last = project_brief_service.last_obs_info(db, project_id)
    stale = _stale_days(last["last_obs_date"], today)

    return {
        "project_id": project_id,
        "suspects": quality["suspects"][:MAX_QUALITY_PER_PROJECT],
        "suspect_count": quality["suspect_count"],
        "missing_count": quality["missing_count"],
        "last_obs_date": last["last_obs_date"],
        "obs_days": last["obs_days"],
        "stale_days": stale,
        "needs_observation": bool(last["last_obs_date"] and stale is not None and stale >= STALE_DAYS),
    }


def all_project_attention(db: Session, today: date | None = None) -> list[dict]:
    """全部项目的关注项汇总（逐项目容错）。"""
    today = today or date.today()
    out: list[dict] = []
    for p in project_repo.list_all(db, include_inactive=False):
        try:
            row = project_attention(db, p.id, today=today)
        except Exception:  # noqa: BLE001 —— 关注项为附加信息，不应拖垮列表
            row = {
                "project_id": p.id,
                "suspects": [],
                "suspect_count": 0,
                "missing_count": 0,
                "last_obs_date": None,
                "obs_days": 0,
                "stale_days": None,
                "needs_observation": False,
            }
        row["project_code"] = p.code
        row["project_name"] = p.name
        out.append(row)
    return out
