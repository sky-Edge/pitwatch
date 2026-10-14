# -*- coding: utf-8 -*-
"""项目模块出入参 schema（Pydantic v2）。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    code: str = Field(min_length=1, max_length=64, description="项目编号，如 PRJ001")
    name: str = Field(min_length=1, max_length=200, description="项目名称")
    location: str | None = Field(default=None, max_length=200, description="工程位置/标段")
    sign_config: dict[str, Any] = Field(
        default_factory=dict,
        description="正负号规则等项目级配置（JSON）",
    )


class ProjectCreate(ProjectBase):
    """新建项目。"""


class ProjectUpdate(BaseModel):
    """更新项目（部分字段可选）。code 变更需通过唯一性校验。"""

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    sign_config: dict[str, Any] | None = None
    is_active: bool | None = Field(default=None, description="True=恢复在用，False=归档")


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
