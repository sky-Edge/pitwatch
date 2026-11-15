# -*- coding: utf-8 -*-
"""合并报表组合的出入参。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReportGroupItemIn(BaseModel):
    """组合里的一项：某张数据表 ← 某个项目。"""

    project_id: int
    sheet_name: str = Field(min_length=1, max_length=100, description="母版里的数据表名")
    sort_order: int = 0


class ReportGroupItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    sheet_name: str
    sort_order: int
    #: 回显用（不是表字段）
    project_name: str | None = None


class ReportGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="组合名，也是导出文件名前缀")
    display_name: str | None = Field(default=None, max_length=100, description="封面上印的工程名称，留空用主项目名")
    template_id: int
    main_project_id: int = Field(description="封面/签字/期次以它为准")
    items: list[ReportGroupItemIn] = Field(default_factory=list, min_length=1)


class ReportGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    template_id: int | None = None
    main_project_id: int | None = None
    items: list[ReportGroupItemIn] | None = None


class ReportGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str | None
    template_id: int
    main_project_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    items: list[ReportGroupItemRead] = Field(default_factory=list)
    template_name: str | None = None
    main_project_name: str | None = None
