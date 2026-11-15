# -*- coding: utf-8 -*-
"""合并报表组合 —— 多个项目合出**一份**监测日报。

【为什么要有它】南北两侧钢栈桥、几个相邻基坑，现场是同一批人、同一天测、装订成一份册子。
一份册子里封面 / 签字页 / 概况 / 分析 / 布点图**只有一份**，数据表各占一张。
原来的导出是"一个项目一份整卷"，两份册子等于封面签字页各印两遍（用户 2026-11-14：
「钢栈桥的数据实在是太多了，打印也是很多，把南北两侧数据总计在一起」）。

【边界】这里只描述"哪张数据表装哪个项目的数据"，**不动项目与测点模型**：
- 数据仍各存各的项目，导入、引擎、预警、单项目导出全部照旧；
- 组合只是"出报时的装订方式"，删掉组合不影响任何观测数据。

【为什么绑在 sheet 上】母版里一张数据表 = 一页纸。南北各一张表，就把"北侧钢栈桥"
绑到「北侧沉降表」、「南侧钢栈桥」绑到「南侧沉降表」。基坑同理：几个基坑各占一张表。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base


class ReportGroup(Base):
    __tablename__ = "report_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: 组合名（也是导出文件名前缀），如「钢栈桥监测日报」
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: 封面/签字页上印的工程名称。留空则用主项目名。
    display_name: Mapped[str | None] = mapped_column(String(100))
    #: 用哪份母版（合并母版：里面有多张数据表）
    template_id: Mapped[int] = mapped_column(ForeignKey("export_templates.id"), nullable=False, index=True)
    #: 主项目：封面 / 签字页 / 概况 / 分析 / 期次 以它为准
    main_project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    items: Mapped[list["ReportGroupItem"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="ReportGroupItem.sort_order",
    )


class ReportGroupItem(Base):
    """组合里的一项：某张数据表 ← 某个项目。"""

    __tablename__ = "report_group_items"
    __table_args__ = (
        # 一张表只能装一个项目；一个项目也只能出现一次（同一项目两张表没有意义，还会撞点号）
        UniqueConstraint("group_id", "sheet_name", name="uq_group_sheet"),
        UniqueConstraint("group_id", "project_id", name="uq_group_project"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("report_groups.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    #: 母版里的数据表名（Sheet 名），如「北侧沉降表」
    sheet_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    group: Mapped[ReportGroup] = relationship(back_populates="items")
