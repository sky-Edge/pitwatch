# -*- coding: utf-8 -*-
"""测点模型。

- 内部唯一 ID：id(PK) + uuid(TEXT UNIQUE)，对外引用一律用内部 ID，
  用户编号 point_no 仅作展示与导入匹配，可变更不影响数据关联。
- point_type：settlement 沉降 / displacement 水平位移 / both 两者。
- initial_value_mm：显式初始值（可选）。V0 计算规则（Phase 4 生效）：
  若配置了 initial_value_mm 则 V0 = 该值，否则 V0 = 首期有效读数。
- status：active 在用 / inactive 停用（保留历史，不物理删除）。
"""
import uuid

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


def _uuid4() -> str:
    return str(uuid.uuid4())


class MeasurementPoint(Base, TimestampMixin):
    __tablename__ = "measurement_points"
    __table_args__ = (
        UniqueConstraint("project_id", "point_no", name="uq_points_project_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=_uuid4, nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    point_no: Mapped[str] = mapped_column(String(32), nullable=False)
    point_type: Mapped[str] = mapped_column(String(16), default="both", nullable=False)
    initial_value_mm: Mapped[float | None] = mapped_column(Float)
    location_desc: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
