# -*- coding: utf-8 -*-
"""项目模型。

- code：项目编号（唯一），如 PRJ001。
- sign_config：JSON 正负号规则（项目级配置，不硬编码）。
  示例：{"settlement": {"positive_means": "down", "desc": "正值=下沉,负值=隆起"},
         "displacement": {"positive_means": "toward_pit", "desc": "正值=向基坑内"}}
- is_active：停用即归档；有观测数据的项目禁止物理删除，只能归档。
"""
from typing import Any

from sqlalchemy import JSON, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    sign_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
