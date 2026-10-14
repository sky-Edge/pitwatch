# -*- coding: utf-8 -*-
"""导出模板（配置类，非观测数据）—— **模板库**形态。

模板**不再属于某个项目**，而是自己声明"我适用于哪些项目"：
- `is_global=True`  → 通用模板，所有项目都能用；
- `template_projects` 关联表 → 这份模板额外绑定到哪几个项目（多对多）。

于是"改一次表头/加一列"只改一份模板，不用每个项目各传一遍
（用户 2026-10-08 提出：「每个项目单独维护一个模板太繁琐了」）。

- project_id：**已废弃**（DEPRECATED），保留仅为兼容与迁移来源；
  新代码一律用 `is_global` + `template_projects` 判断可用性。
- file_path：原件副本。新模板落在 `templates/_library/{id}/`；
  **老模板文件原地不动**（file_path 是绝对路径，搬迁等于造断链风险）。
- layout_json：模板单元格定位规则（列关键词自动识别 + 人工修正），供导出处填写。
- is_builtin：内置标记，内置不可删、不可改。
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ExportTemplate(Base):
    __tablename__ = "export_templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: DEPRECATED —— 见模块 docstring。模板独立化之后不再用它判可用性。
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    #: 通用模板：所有项目可见（比"绑定 0 个项目"语义明确 —— 后者分不清"还没配"还是"故意通用"）
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    template_type: Mapped[str] = mapped_column(String(10), default="excel")  # excel / word
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    file_path: Mapped[str | None] = mapped_column(String(400))  # 原件副本；内置为空
    layout_json: Mapped[dict | None] = mapped_column(JSON)  # 单元格定位规则
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    @property
    def is_usable_everywhere(self) -> bool:
        """内置（无原件、走系统标准版式）或通用模板 → 所有项目都能用。"""
        return bool(self.is_builtin or self.is_global)
