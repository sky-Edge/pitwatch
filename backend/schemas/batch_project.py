# -*- coding: utf-8 -*-
"""批量建项目（一份清单 → 拆多个基坑项目）出入参 schema。"""
from datetime import date

from pydantic import BaseModel, Field


class BatchPointIn(BaseModel):
    """一个测点。坐标与高程统一 mm（解析阶段已从米换算）。"""

    point_no: str = Field(min_length=1, max_length=32, description="测点编号，如 15号1")
    x: float | None = Field(default=None, description="X 坐标(mm)")
    y: float | None = Field(default=None, description="Y 坐标(mm)")
    z: float | None = Field(default=None, description="高程(mm) → 沉降初始读数")


class BatchGroupIn(BaseModel):
    """用户在界面上分好的一组（= 一个基坑 = 一个项目）。"""

    group_name: str = Field(default="", description="界面上的分组名（默认取点号前缀）")
    project_code: str = Field(default="", description="项目编号；已存在则复用该项目")
    project_name: str = Field(default="", description="项目名称；留空用编号")
    obs_date: date = Field(description="这组初始值采集日期（第 0 期）")
    points: list[BatchPointIn] = Field(default_factory=list)


class BatchCommitRequest(BaseModel):
    groups: list[BatchGroupIn] = Field(default_factory=list)
