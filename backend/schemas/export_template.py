# -*- coding: utf-8 -*-
"""导出模板 schema。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExportTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    name: str
    template_type: str
    is_builtin: bool
    #: 通用模板：所有项目都能用（模板独立化之后，适用范围由它 + project_ids 决定）
    is_global: bool = False
    #: 这份模板绑定了哪些项目（内置/通用时可能为空）
    project_ids: list[int] = []
    project_names: list[str] = []
    file_path: str | None
    layout_json: dict | None
    file_ok: bool | None = None  # 项目模板：原件文件是否存在（前端显示"文件缺失"）
    created_at: datetime


class LayoutFieldConfig(BaseModel):
    """单 Sheet 数据字段 → 列号 定位。"""

    point_col: int | None = None
    v0_m: int | None = None
    prev_m: int | None = None
    current_m: int | None = None
    prev_cum_mm: int | None = None
    cum_mm: int | None = None
    delta_mm: int | None = None
    rate: int | None = None
    control_cum: int | None = None
    control_rate: int | None = None
    warning_level: int | None = None
    data_start_row: int = 7


class SheetLayout(BaseModel):
    sheet_name: str
    layout: LayoutFieldConfig
    mode: str = "per_row"  # per_row / x_y_two_rows（同一 Sheet X/Y 上下两行）


class ExportLayout(BaseModel):
    """导出布局：文件级元信息 + Sheet 定位。"""

    meta: dict = {}  # title_cell / date_cell / project_name_cell 等
    sheets: list[SheetLayout] = []


class TemplateUpdate(BaseModel):
    """更新模板（名称 / 布局）。"""

    name: str | None = None
    layout_json: dict | None = None


class TemplateScopeUpdate(BaseModel):
    """设置模板的适用范围：绑定的项目 + 是否通用。

    两项都可只传其一（`None` = 不改）。**不允许配成"哪儿都用不了"**（见服务层防呆）。
    """

    project_ids: list[int] | None = None
    is_global: bool | None = None
