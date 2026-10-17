# -*- coding: utf-8 -*-
"""预警规则**条目**（一套规则里的每一行）。

- `set_id` → 属于哪套规则（`warning_rule_sets`），**这是唯一的归属**。
- project_id：**已废弃**（DEPRECATED）。改之前规则挂在项目下，实测 8 个项目
  各抄了同一套数；现在规则独立成"规则库"，由套声明适用项目。
  保留该列只为"代码回退到旧版本时行为不变"，新代码一律写 NULL。
- indicator：settlement(沉降) / displacement(水平位移)。
- level：control(控制值/预警) / alarm(报警值)。
- 判断逻辑（Warning Engine，Phase 7）：|累计变化| ≥ alarm → 报警；≥ control → 预警。
- 三**维**：累计变化量 / **本次变化量 ΔV**（`threshold_delta_mm`，2026-10-16 加）/ 变化速率。
  ΔV 那维**只参与判定，不印在报表上** —— 表上「控制值」仍是累计、速率两列，用户定的口径是
  "表里印的数 = 系统判预警的数"，加第三列等于动母版/标准版式，不划算。
- 规则完全配置化，禁止硬编码；数值单位 mm、mm/day。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class WarningRule(Base):
    __tablename__ = "warning_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: 所属规则（一套 = 各指标 × 各等级）。迁移后必填。
    set_id: Mapped[int | None] = mapped_column(
        ForeignKey("warning_rule_sets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: DEPRECATED —— 见模块 docstring；归属一律看 set_id。
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    indicator: Mapped[str] = mapped_column(String(16), nullable=False)  # settlement/displacement
    level: Mapped[str] = mapped_column(String(16), nullable=False)      # control/alarm
    threshold_cumulative_mm: Mapped[float | None] = mapped_column(Float)  # 累计变化限值
    #: 本次变化量 ΔV 限值(mm)：这一期相对上期最近有效值"跳了多少"的上限。
    #: 与速率的分工：速率 = ΔV/间隔天数，隔 3 天测一次会被摊薄；ΔV 不看间隔，
    #: 隔多久测都按同一个上限卡 —— 用户 2026-10-16 要的就是这一档。
    #: 老库没有这一列 → 迁移补 NULL（NULL = 这一维不参与判定，与另外两维同一口径）。
    threshold_delta_mm: Mapped[float | None] = mapped_column(Float)
    threshold_rate_mm_per_day: Mapped[float | None] = mapped_column(Float)  # 速率限值
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_note: Mapped[str | None] = mapped_column(String(200))  # 规范来源，如 GB 50497-2019
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
