# -*- coding: utf-8 -*-
"""每日进度：今天哪些项目「填了数据」、哪些「出过报」。

用户 2026-11-16：项目列表要能一眼区分"今天做了哪些项目、没做哪些"，
而且**填数据 + 打印两件都做完**才算做完。

- 「填数据」不看导出日志：直接查当天有没有**非系统生成**的观测记录
  （generated 是演示数据，填它不算干过活；作废/被更正的不算当前成果）。
- 「出报」只能靠导出日志：这个动作原本没有任何留痕，导完文件就下载走了，
  所以在导出接口里落一条 `export_logs`。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from backend.models.export_log import ExportLog
from backend.models.observation_record import ObservationRecord
from backend.models.point import MeasurementPoint


def record_export(db: Session, project_id: int, obs_dates: list[str], export_type: str) -> None:
    """导出一条日报就记一条。**失败不能影响导出本身** —— 日志丢了大不了进度显示不准。"""
    try:
        db.add(
            ExportLog(
                project_id=project_id,
                obs_dates=",".join(obs_dates)[:2000],
                export_type=export_type,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def daily_progress(db: Session, day: date) -> dict:
    """某一天的项目进度：填了数据的 id、出过报的 id。"""
    day_start = datetime.combine(day, time.min)
    day_end = day_start + timedelta(days=1)

    filled = {
        pid
        for (pid,) in db.execute(
            select(distinct(MeasurementPoint.project_id))
            .join(ObservationRecord, ObservationRecord.point_id == MeasurementPoint.id)
            .where(
                ObservationRecord.obs_date == day,
                # 演示数据不算"今天干了活"
                ObservationRecord.source != "generated",
                # 被更正掉的、作废的不算当前成果
                ObservationRecord.status.notin_(("superseded", "void")),
            )
        ).all()
    }

    exported = {
        pid
        for (pid,) in db.execute(
            select(distinct(ExportLog.project_id)).where(
                ExportLog.created_at >= day_start,
                ExportLog.created_at < day_end,
            )
        ).all()
    }

    return {
        "date": day.isoformat(),
        "filled": sorted(filled),
        "exported": sorted(exported),
        # 两件都做了才算今天完成
        "done": sorted(filled & exported),
    }
