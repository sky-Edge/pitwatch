# -*- coding: utf-8 -*-
"""观测记录（原始数据，只增不改 + 可更正）。

模板实测驱动的模型设计（见 MEMORY.md）：
- series_type: settlement(沉降) / displacement(水平位移) —— 同一测点可能两种都测
- component:    value(沉降读数) / X / Y(水平位移分量) —— 模板中位移点每期占 2 行
- value_mm:     统一 mm 存储（模板测值单位为 m，导入时 ×1000）
- status:       normal / missing / suspect / void —— 异常只标记不删除
- superseded_by_id: 更正链。NULL=当前生效；非 NULL=已被该记录更正替代（原值永久保留）

防重设计（关键）：
- 部分唯一索引 uq_obs_active：仅对"生效记录"(superseded_by_id IS NULL)
  约束 (point_id, series_type, component, obs_date) 唯一。
  效果：正常录入同日同分量撞车 → 409；更正后旧记录让位 → 新记录可正常插入。

本表为原始数据表：不允许 UPDATE 数值、不允许物理 DELETE，
仅允许：改 remark/status、更正（生成新记录并让旧记录指向它）。
"""
from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class ObservationRecord(Base, TimestampMixin):
    __tablename__ = "observation_records"
    __table_args__ = (
        Index("ix_obs_point_date", "point_id", "obs_date"),
        # 仅生效记录唯一：status=superseded/void 的记录不占用唯一索引，更正后旧记录让位
        Index(
            "uq_obs_active",
            "point_id",
            "series_type",
            "component",
            "obs_date",
            unique=True,
            sqlite_where=text("status NOT IN ('superseded', 'void')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("measurement_points.id"), nullable=False)
    series_type: Mapped[str] = mapped_column(String(16), nullable=False)  # settlement/displacement
    component: Mapped[str] = mapped_column(String(8), nullable=False)     # value/X/Y
    obs_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_mm: Mapped[float | None] = mapped_column(Float)  # NULL = 缺测
    status: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # manual/excel/correct
    import_batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("import_batches.id"))
    superseded_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("observation_records.id"), default=None
    )  # NULL=生效；非 NULL=已被该记录更正替代
    remark: Mapped[str | None] = mapped_column(String(500))
