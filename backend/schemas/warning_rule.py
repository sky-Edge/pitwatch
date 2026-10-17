# -*- coding: utf-8 -*-
"""预警规则出入参 schema。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Indicator = Literal["settlement", "displacement"]
RuleLevel = Literal["control", "alarm"]

#: 参与"报警值不得低于控制值"比对的三维：(字段名, 界面上的叫法)
DIM_FIELDS: tuple[tuple[str, str], ...] = (
    ("threshold_cumulative_mm", "累计变化量"),
    ("threshold_delta_mm", "本次变化量"),
    ("threshold_rate_mm_per_day", "变化速率"),
)


class WarningRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    indicator: str
    level: str
    threshold_cumulative_mm: float | None
    threshold_delta_mm: float | None
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
    #: 本次变化量 ΔV 上限：不看观测间隔，隔几天测都按这一个数卡。
    #: 只参与判定，不印到报表的「控制值」列上（那两列仍是累计/速率）。
    threshold_delta_mm: float | None = Field(default=None, ge=0, description="本次变化量限值(mm)")
    threshold_rate_mm_per_day: float | None = Field(default=None, ge=0, description="变化速率限值(mm/d)")
    is_active: bool = True
    source_note: str | None = Field(default=None, max_length=200, description="规范来源")


class WarningRuleBatchWrite(BaseModel):
    """项目规则整组覆盖：沉降/位移 × 控制/报警 各一条（每条里三个维度）。"""

    rules: list[WarningRuleWrite] = Field(min_length=1, max_length=8)
    source_note: str | None = Field(default=None, max_length=200, description="来源（规范名称），可统一标注")

    @model_validator(mode="after")
    def _alarm_not_below_control(self) -> "WarningRuleBatchWrite":
        """报警值 < 控制值 → 拒收。

        为什么必须拦（2026-10-16）：`check_level` 先看报警值，报警值反倒更低时
        任何超限都会直接落进「报警」，**「预警」这一级永远出不来** —— 界面上数字都填了、
        系统看着像双控，实际只剩一级，而且一声不吭。这类配置错误没有报错、没有异常，
        只有事后翻 Word 才发现"怎么一超就报警"。
        """
        by_ind: dict[str, dict[str, WarningRuleWrite]] = {}
        for r in self.rules:
            by_ind.setdefault(r.indicator, {})[r.level] = r
        cn = {"settlement": "沉降", "displacement": "水平位移"}
        bad: list[str] = []
        for ind, levels in by_ind.items():
            ctrl, alarm = levels.get("control"), levels.get("alarm")
            if not ctrl or not alarm:
                continue
            for field, dim in DIM_FIELDS:
                c, a = getattr(ctrl, field), getattr(alarm, field)
                if c is not None and a is not None and a < c:
                    bad.append(f"{cn.get(ind, ind)}·{dim}：报警值 {a:g} < 控制值 {c:g}")
        if bad:
            raise ValueError(
                "报警值不能低于控制值，否则「预警」这一级永远不会出现（一超就直接报警）。请改大报警值、"
                "或改小控制值 —— " + "；".join(bad)
            )
        return self


def batch_error_text(exc: ValidationError) -> str:
    """把整组规则的校验错误压成一句能直接给用户读的话。

    pydantic 在 `msg` 前面塞了 `"Value error, "`，并且把细节装在自定义异常里 ——
    原样透传到界面就是一句英文 + 一坨 JSON。这里只留人话，多个维度一起倒挂时全列出来。
    """
    parts: list[str] = []
    for e in exc.errors():
        msg = str(e.get("msg") or "").removeprefix("Value error, ")
        if msg and msg not in parts:
            parts.append(msg)
    return "；".join(parts) or "预警规则参数有误"


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
