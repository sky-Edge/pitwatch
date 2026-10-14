# -*- coding: utf-8 -*-
"""测点业务服务。

业务规则：
- 所属项目必须存在（404）。
- point_no 项目内唯一（409）。
- 删除保护：测点一律停用（inactive），不物理删除，历史观测数据保留。
- 批量新建：先预览（不落库）后确认；重复编号跳过并报告。
"""
import re

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.models.point import MeasurementPoint
from backend.repositories import point_repo
from backend.schemas.point import BatchPointItem, PointCreate, PointUpdate
from backend.services import project_service
from backend.schemas.point import (  # noqa: F401  （批处理返回类型无需在 service 显式引用）
    BatchCreateResponse,
    BatchPointCreateRequest,
    BatchPointPreviewRequest,
    BatchPreviewResponse,
)


def list_points(db: Session, project_id: int, include_inactive: bool = True) -> list[MeasurementPoint]:
    project_service.get_project(db, project_id)  # 项目必须存在
    return point_repo.list_by_project(db, project_id, include_inactive=include_inactive)


def get_point(db: Session, point_id: int) -> MeasurementPoint:
    point = point_repo.get(db, point_id)
    if point is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"测点 {point_id} 不存在",
        )
    return point


def create_point(db: Session, project_id: int, data: PointCreate) -> MeasurementPoint:
    project_service.get_project(db, project_id)  # 项目必须存在
    if point_repo.get_by_no(db, project_id, data.point_no):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目 {project_id} 中已存在测点编号 {data.point_no}",
        )
    return point_repo.create(db, project_id, data)


def update_point(db: Session, point_id: int, data: PointUpdate) -> MeasurementPoint:
    point = get_point(db, point_id)
    if data.point_no and data.point_no != point.point_no:
        if point_repo.get_by_no(db, point.project_id, data.point_no):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"项目 {point.project_id} 中已存在测点编号 {data.point_no}",
            )
    return point_repo.update(db, point, data)


def archive_point(db: Session, point_id: int) -> MeasurementPoint:
    """删除保护：停用归档，不物理删除。"""
    point = get_point(db, point_id)
    return point_repo.soft_delete(db, point)


# ---------------- 批量新建测点 ----------------

_POINT_NO_PATTERN = re.compile(r"^[A-Za-z0-9\-_./]+$")


def _validate_point_item(item: BatchPointItem) -> str | None:
    """返回校验错误信息；None=通过。编号规则与导入白名单保持一致。"""
    if not item.point_no or not item.point_no.strip():
        return "编号不能为空"
    if len(item.point_no) > 32:
        return "编号超长（≤32 字符）"
    if not _POINT_NO_PATTERN.match(item.point_no):
        return "编号只能含字母/数字/横线/点/下划线"
    return None


def batch_preview_points(db: Session, project_id: int, items: list[BatchPointItem]) -> dict:
    """批量校验（不落库）。返回 valid/conflict/error 三清单。"""
    project_service.get_project(db, project_id)
    existing = {p.point_no for p in point_repo.list_by_project(db, project_id, include_inactive=True)}
    seen: set[str] = set()  # 批内去重

    valid: list[dict] = []
    conflict: list[dict] = []
    error: list[dict] = []

    for i, item in enumerate(items):
        err = _validate_point_item(item)
        base = {"index": i, "point_no": item.point_no}
        if err:
            error.append({**base, "message": err, "reason": "format"})
            continue
        if item.point_no in seen:
            conflict.append({**base, "message": "批内重复：本批中已有同名行", "reason": "dup_in_batch"})
            continue
        seen.add(item.point_no)
        if item.point_no in existing:
            conflict.append({**base, "message": "项目内已存在该测点编号", "reason": "exists"})
            continue
        valid.append({**base, "message": "可创建", "point": item.model_dump()})

    return {"valid": valid, "conflict": conflict, "error": error}


def batch_create_points(db: Session, project_id: int, items: list[BatchPointItem]) -> dict:
    """批量落库（事务级逐条，部分成功）。重复编号跳过不报错。"""
    project_service.get_project(db, project_id)
    existing = {p.point_no for p in point_repo.list_by_project(db, project_id, include_inactive=True)}

    created_rows: list[dict] = []
    skipped_rows: list[dict] = []
    failed_rows: list[dict] = []
    seen: set[str] = set()

    for i, item in enumerate(items):
        base = {"index": i, "point_no": item.point_no}
        err = _validate_point_item(item)
        if err:
            failed_rows.append({**base, "success": False, "message": err, "point_id": None})
            continue
        if item.point_no in seen or item.point_no in existing:
            skipped_rows.append({**base, "success": False, "message": "已存在，跳过", "point_id": None})
            continue
        seen.add(item.point_no)
        try:
            pt = point_repo.create(db, project_id, item)
            created_rows.append({**base, "success": True, "message": "已创建", "point_id": pt.id})
        except Exception as e:  # noqa: BLE001
            db.rollback()
            failed_rows.append({**base, "success": False, "message": f"创建失败：{e}", "point_id": None})

    db.commit()
    return {
        "created": len(created_rows),
        "created_rows": created_rows,
        "skipped": skipped_rows,
        "failed": failed_rows,
    }
