# -*- coding: utf-8 -*-
"""测点模块出入参 schema（Pydantic v2）。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PointType = Literal["settlement", "displacement", "both"]
PointStatus = Literal["active", "inactive"]


class PointBase(BaseModel):
    point_no: str = Field(min_length=1, max_length=32, description="用户编号，如 LZ-01")
    point_type: PointType = Field(default="both", description="settlement/displacement/both")
    initial_value_mm: float | None = Field(default=None, description="显式初始值(mm)，可选")
    location_desc: str | None = Field(default=None, max_length=200, description="桩号/位置描述")


class PointCreate(PointBase):
    """新建测点。"""


class PointUpdate(BaseModel):
    """更新测点（部分字段可选）。point_no 变更需通过项目内唯一性校验。"""

    point_no: str | None = Field(default=None, min_length=1, max_length=32)
    point_type: PointType | None = None
    initial_value_mm: float | None = None
    location_desc: str | None = Field(default=None, max_length=200)
    status: PointStatus | None = None


class PointRead(PointBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    project_id: int
    status: PointStatus
    created_at: datetime
    updated_at: datetime


class BatchPointItem(PointBase):
    """批量新建的单个测点（与单点同字段）。"""


class BatchPointPreviewRequest(BaseModel):
    """批量预览入参：全部待建行。"""

    items: list[BatchPointItem] = Field(default_factory=list, max_length=200, description="单批最多 200 个")


class BatchPreviewRow(BaseModel):
    """预览结果单行。"""

    index: int
    status: Literal["valid", "conflict", "error"]
    message: str
    point: BatchPointItem | None = None


class BatchPreviewResponse(BaseModel):
    valid: list[BatchPreviewRow]
    conflict: list[BatchPreviewRow]
    error: list[BatchPreviewRow]


class BatchPointCreateRequest(BaseModel):
    """批量落库入参：只应包含预览中 valid 的行。"""

    items: list[BatchPointItem] = Field(default_factory=list, max_length=200)


class BatchCreateRow(BaseModel):
    """批量落库单行结果。"""

    index: int
    point_no: str
    success: bool
    message: str
    point_id: int | None = None


class BatchCreateResponse(BaseModel):
    created: int
    created_rows: list[BatchCreateRow]
    skipped: list[BatchCreateRow]  # 已存在等被跳过的
    failed: list[BatchCreateRow]
