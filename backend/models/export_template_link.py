# -*- coding: utf-8 -*-
"""模板 ↔ 项目 关联表（多对多）。

模板独立成"模板库"之后，**由模板声明它作用于哪些项目**：
一份母版可以同时绑给好几个基坑（它们共用同一个版式），
也可以置成通用（`ExportTemplate.is_global=True`）而不绑任何项目。

为什么用关联表而不是在 `export_templates` 上加几个 project_id 列：
- 一个模板绑几个项目是**可变的**，列数写死会在"再多绑一个"时崩掉；
- 反过来，一个项目也会用多份模板（基坑日报 + 钢栈桥日报）。

级联：模板删除 → 关联随之删除；项目删除 → 关联随之删除（见 `hard_delete_service`，
那里会先判断"模板是否还被别的项目用着"，只解绑、不误删共用模板）。
"""
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ExportTemplateProject(Base):
    __tablename__ = "template_projects"

    template_id: Mapped[int] = mapped_column(
        ForeignKey("export_templates.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True, index=True
    )
