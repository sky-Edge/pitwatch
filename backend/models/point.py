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


#: point_type → 这个点会进哪几张观测表（both = 沉降表和位移表里都有它一行）
SERIES_TYPES: dict[str, tuple[str, ...]] = {
    "settlement": ("settlement",),
    "displacement": ("displacement",),
    "both": ("settlement", "displacement"),
}


def series_types_of(points) -> set[str]:
    """这批测点里实际会出现**哪几类观测**。

    报表上一组「控制值」列对应一类观测，所以"这份文档要印哪几组控制值"、
    "缺规则该拦哪几类"都以它为准 —— 项目只有沉降点时不该要求水平位移的规则。
    """
    out: set[str] = set()
    for p in points:
        out.update(SERIES_TYPES.get(p.point_type, ()))
    return out


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
