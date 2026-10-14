# -*- coding: utf-8 -*-
"""预警规则出入参 schema。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Indicator = Literal["settlement", "displacement"]
RuleLevel = Literal["control", "alarm"]


class WarningRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    indicator: str
    level: str
    threshold_cumulative_mm: float | None
    threshold_rate_mm_per_day: float | None
    is_active: bool
    source_note: str | None
    created_at: datetime
    updated_at: datetime


class WarningRuleWrite(BaseModel):
    """单条规则写入。"""

    indicator: Indicator
    level: RuleLevel
    threshold_cumulative_mm: float | None = Field(default=None, ge=0, description="累计变化限值(mm)")
    threshold_rate_mm_per_day: float | None = Field(default=None, ge=0, description="变化速率限值(mm/d)")
    is_active: bool = True
    source_note: str | None = Field(default=None, max_length=200, description="规范来源")


class WarningRuleBatchWrite(BaseModel):
    """项目规则整组覆盖：沉降/位移 × 控制/报警 各一条。"""

    rules: list[WarningRuleWrite] = Field(min_length=1, max_length=8)
    source_note: str | None = Field(default=None, max_length=200, description="来源（规范名称），可统一标注")


class StandardPreset(BaseModel):
    """规范预设模板（只读，来自 backend/services/standards.py）。"""

    key: str
    name: str
    description: str
    rules: list[WarningRuleWrite]


# ─────────────── 规则库（一套规则 = 各指标 × 各等级） ───────────────


class WarningRuleSetRead(BaseModel):
    """一套预警规则 + 它作用于哪些项目。"""

    id: int
    name: str
    source_note: str | None = None
    #: 通用规则：没绑任何规则的项目由它兜底
    is_global: bool = False
    project_ids: list[int] = Field(default_factory=list)
    project_names: list[str] = Field(default_factory=list)
    rules: list[WarningRuleRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WarningRuleSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rules: list[WarningRuleWrite] = Field(min_length=1, max_length=8)
    source_note: str | None = Field(default=None, max_length=200)
    project_ids: list[int] = Field(default_factory=list, description="适用项目；留空且非通用 → 只能靠通用兜底")
    is_global: bool = False


class WarningRuleSetUpdate(BaseModel):
    """改名称 / 改依据 / 改限值（只传要改的）。"""

    name: str | None = Field(default=None, max_length=100)
    source_note: str | None = Field(default=None, max_length=200)
    rules: list[WarningRuleWrite] | None = Field(default=None, min_length=1, max_length=8)


class WarningRuleSetScopeUpdate(BaseModel):
    """设置适用范围：勾哪些项目 + 是否通用。"""

    project_ids: list[int] | None = None
    is_global: bool | None = None


class ProjectRuleSetInfo(BaseModel):
    """某项目当前用的是哪套规则（界面据此提示"改了会分叉"）。"""

    set_id: int | None = None
    name: str | None = None
    is_global: bool = False
    #: 这套规则还被几个项目用着（>1 表示从项目侧改会**自动新建一套只给本项目**）
    shared_project_count: int = 0
