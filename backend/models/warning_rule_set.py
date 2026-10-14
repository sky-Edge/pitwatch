# -*- coding: utf-8 -*-
"""预警规则库：**一套规则 = 各指标 × 各等级的一组限值**（现状是 2 指标 × 2 等级 = 4 条）。

为什么独立出来（用户 2026-10-09）：
    「把预警规则提出来 直接作用于系统 用预警规则来选择项目」

改之前 `warning_rules.project_id` 把规则钉死在单个项目上 —— 实测生产库：
**8 个项目的规则是同一套数**（沉降 30/10 + 40/12，位移 20/3 + 30/4）各抄了一遍，共 32 行；
还有 4 个项目（六号左边、九号、七号左边、7号右边）**一条规则都没有 → 根本不判预警**。

改之后：规则自己声明"我作用于哪些项目"（多对多 + 通用开关），
一个基坑一套方案，改一次全体跟随 —— 与「模板库」同一套路数。

- `is_global=True` → 通用规则：**没绑任何规则的项目由它兜底**。
- 一个项目**最多绑一套**规则（现场就是一个基坑一套监测方案）；
  违反时由服务层拦截并提示，不做"两套合并"这种说不清的事。
- ⚠ `WarningRule.project_id` **已废弃**：迁移后一律置 NULL，新代码只认 `set_id`。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class WarningRuleSet(Base):
    __tablename__ = "warning_rule_sets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: 规则名称（全局唯一 —— 界面下拉里出现两个同名规则没法选）
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    #: 依据来源，如"GB 50497-2019"或"八号基坑监测方案"
    source_note: Mapped[str | None] = mapped_column(String(200))
    #: 通用规则：没有被任何项目绑定时用它兜底
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WarningRuleSetProject(Base):
    """规则 ↔ 项目 关联：这套规则作用于哪些项目。"""

    __tablename__ = "warning_rule_set_projects"

    set_id: Mapped[int] = mapped_column(
        ForeignKey("warning_rule_sets.id", ondelete="CASCADE"), primary_key=True
    )
    #: 唯一约束：**一个项目最多绑一套规则**（服务层会先把该项目从别的套上解绑）
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True, unique=True, index=True
    )
