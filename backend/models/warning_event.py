# -*- coding: utf-8 -*-
"""预警事件（派生表，可整体清空重建 —— 计算结果类数据）。

与 calculation_results 同属「派生数据」：随重算（recompute）整体清空重建，
由 Warning Engine 生成，只记录触发的事件（control/alarm），不记录 normal。

- project_id：所属项目（冗余，便于项目级查询/清理）
- point_id / obs_date：事件点位与日期
- indicator / level / trigger_type：指标 / 等级 / 触发项（cumulative=累计 / rate=速率）
- value_mm：触发时对应的值（累计值或速率值）
- message：人类可读描述
- recompute_batch：重算批次号（留痕，与 calculation_results 一致）
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class WarningEvent(Base):
    __tablename__ = "warning_events"
    __table_args__ = (
        Index("ix_warning_project_date", "project_id", "obs_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    point_id: Mapped[int] = mapped_column(ForeignKey("measurement_points.id"), nullable=False)
    series_type: Mapped[str] = mapped_column(String(16), nullable=False)  # settlement/displacement
    component: Mapped[str] = mapped_column(String(8), nullable=False)     # value/X/Y
    obs_date: Mapped[date] = mapped_column(Date, nullable=False)
    indicator: Mapped[str] = mapped_column(String(16), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)        # control/alarm
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)  # cumulative/rate
    value_mm: Mapped[float | None] = mapped_column(Float)
    rate_mm_per_day: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str] = mapped_column(String(300), default="")
    recompute_batch: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
