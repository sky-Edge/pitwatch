# -*- coding: utf-8 -*-
"""观测记录出入参 schema（Pydantic v2）。

约定：
- series_type: settlement | displacement
- component:   settlement→value；displacement→X|Y
- value_mm:    统一 mm；None 表示缺测
- 原始数据不可改数值：RecordUpdate 只允许 remark / status
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SeriesType = Literal["settlement", "displacement"]
Component = Literal["value", "X", "Y"]
RecordStatus = Literal["normal", "missing", "suspect", "void"]


def validate_component_for_series(series_type: str, component: str) -> None:
    """series_type 与 component 组合合法性。"""
    if series_type == "settlement" and component != "value":
        raise ValueError("沉降(settlement)的 component 只能是 value")
    if series_type == "displacement" and component not in ("X", "Y"):
        raise ValueError("水平位移(displacement)的 component 只能是 X 或 Y")


class RecordCreate(BaseModel):
    """单条观测录入。value_mm 缺省时按缺测处理。"""

    series_type: SeriesType
    component: Component
    obs_date: date
    value_mm: float | None = Field(default=None, description="统一 mm；None=缺测")
    remark: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_combination(self):
        validate_component_for_series(self.series_type, self.component)
        return self


class RecordUpdate(BaseModel):
    """仅允许改 remark / status —— 数值与日期是原始数据，禁止修改（extra=forbid 防绕过）。"""

    model_config = ConfigDict(extra="forbid")

    remark: str | None = None
    status: RecordStatus | None = None


class RecordCorrection(BaseModel):
    """更正请求：填错时生成一条新记录，旧记录自动指向新记录（原值保留）。

    更正不修改原始记录数值，只把它的 superseded_by_id 指向新记录。
    """

    value_mm: float | None = Field(description="更正后的读数，统一 mm；None=更正为缺测")
    remark: str | None = Field(default=None, max_length=500, description="更正说明（必填建议）")


class RecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    point_id: int
    series_type: str
    component: str
    obs_date: date
    value_mm: float | None
    status: str
    source: str
    import_batch_id: int | None
    superseded_by_id: int | None
    remark: str | None
    created_at: datetime
    updated_at: datetime
