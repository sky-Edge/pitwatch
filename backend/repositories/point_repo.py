# -*- coding: utf-8 -*-
"""测点数据访问（repository）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.point import MeasurementPoint
from backend.schemas.point import PointCreate, PointUpdate


def get(db: Session, point_id: int) -> MeasurementPoint | None:
    return db.get(MeasurementPoint, point_id)


def get_by_no(db: Session, project_id: int, point_no: str) -> MeasurementPoint | None:
    return db.scalar(
        select(MeasurementPoint).where(
            MeasurementPoint.project_id == project_id,
            MeasurementPoint.point_no == point_no,
        )
    )


def list_by_project(db: Session, project_id: int, include_inactive: bool = True) -> list[MeasurementPoint]:
    stmt = (
        select(MeasurementPoint)
        .where(MeasurementPoint.project_id == project_id)
        .order_by(MeasurementPoint.point_no)
    )
    if not include_inactive:
        stmt = stmt.where(MeasurementPoint.status == "active")
    return list(db.scalars(stmt))


def create(db: Session, project_id: int, data: PointCreate) -> MeasurementPoint:
    point = MeasurementPoint(project_id=project_id, **data.model_dump())
    db.add(point)
    db.commit()
    db.refresh(point)
    return point


def update(db: Session, point: MeasurementPoint, data: PointUpdate) -> MeasurementPoint:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(point, field, value)
    db.commit()
    db.refresh(point)
    return point


def soft_delete(db: Session, point: MeasurementPoint) -> MeasurementPoint:
    """停用：status=inactive，保留数据。"""
    point.status = "inactive"
    db.commit()
    db.refresh(point)
    return point
