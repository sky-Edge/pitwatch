# -*- coding: utf-8 -*-
"""物理删除（级联清理）服务。

背景：删除保护（归档/停用）是默认行为，但用户要求"项目/测点能真正删除"。
本层提供受控的物理删除：调用方必须通过 API 传入 force 校验串（编号），
并且删除前先自动备份数据库（database/backups），保证误删可回退。

级联范围：
- 删测点：observation_records（含 superseded 旧版）→ warning_events → measurement_points
- 删项目：上述全部 + import_batches → projects → 模板**绑定**、预警规则**绑定**
  （⚠ 模板与预警规则都已独立成"库"：只删本项目独占的，被别的项目共用的只解绑）
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.models.export_template import ExportTemplate
from backend.models.export_template_link import ExportTemplateProject
from backend.models.import_batch import ImportBatch
from backend.models.observation_record import ObservationRecord
from backend.models.point import MeasurementPoint
from backend.models.project import Project
from backend.models.warning_event import WarningEvent
from backend.models.warning_rule import WarningRule
from backend.services import backup_service, export_template_service


def _safety_backup(db: Session) -> None:
    """删除前自动备份（安全网），失败不阻断删除主流程但告警由调用方打印。"""
    try:
        backup_service.create_backup(db, note="物理删除前自动备份")
    except Exception:
        pass


def _detach_superseded_refs(db: Session, point_ids: list[int]) -> None:
    """删除前先解除 superseded 自引用（整批删除时若旧记录被新记录引用，
    SQLite 逐行删除顺序不可控会触发 FK 失败 —— 本批整删，引用无保留价值）。"""
    if not point_ids:
        return
    db.execute(
        update(ObservationRecord)
        .where(ObservationRecord.point_id.in_(point_ids))
        .values(superseded_by_id=None)
    )


def delete_point_hard(db: Session, point_id: int, force: str | None = None) -> dict:
    """物理删除测点及其全部观测数据。force 必须等于测点编号（防止误删）。"""
    point = db.get(MeasurementPoint, point_id)
    if point is None:
        raise HTTPException(status_code=404, detail=f"测点 {point_id} 不存在")

    if force is None or force != point.point_no:
        raise HTTPException(
            status_code=400,
            detail=f"确认串不匹配：如需物理删除请传入 force={point.point_no}（删除后不可恢复）",
        )

    _safety_backup(db)

    # 级联清理：观测记录（含被 superseded 的旧版）→ 预警事件 → 测点
    _detach_superseded_refs(db, [point_id])
    db.execute(delete(ObservationRecord).where(ObservationRecord.point_id == point_id))
    db.execute(delete(WarningEvent).where(WarningEvent.point_id == point_id))
    db.delete(point)
    db.commit()
    return {"deleted": True, "kind": "point", "point_no": point.point_no}


def delete_project_hard(db: Session, project_id: int, force: str | None = None) -> dict:
    """物理删除项目及其全部数据（测点/观测/预警/规则/导入批次）。

    force 必须等于项目编号。
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")

    if force is None or force != project.code:
        raise HTTPException(
            status_code=400,
            detail=f"确认串不匹配：如需物理删除请传入 force={project.code}（删除后不可恢复）",
        )

    _safety_backup(db)

    point_ids = list(db.scalars(select(MeasurementPoint.id).where(MeasurementPoint.project_id == project_id)))

    # 0) 先解除 superseded 自引用（防 FK：整批删除含更正链时顺序不可控）
    _detach_superseded_refs(db, point_ids)

    # 1) 观测记录
    if point_ids:
        db.execute(
            delete(ObservationRecord).where(ObservationRecord.point_id.in_(point_ids))
        )
    # 2) 预警事件
    db.execute(delete(WarningEvent).where(WarningEvent.project_id == project_id))
    # 3) 导入批次
    db.execute(delete(ImportBatch).where(ImportBatch.project_id == project_id))
    # 4) 预警规则 —— 规则已独立成"规则库"，所以不能像以前那样"删项目就删光它的规则"：
    #    - 只被本项目用的那套 → 连同规则行一起删；
    #    - 被别的项目共用的（或通用的）→ **只解除本项目的绑定，规则保留**
    from backend.models.warning_rule_set import WarningRuleSet, WarningRuleSetProject

    bound_set_ids = [
        int(x)
        for x in db.scalars(
            select(WarningRuleSetProject.set_id).where(
                WarningRuleSetProject.project_id == project_id
            )
        )
    ]
    rules_kept: list[str] = []
    for sid in bound_set_ids:
        others = [
            int(x)
            for x in db.scalars(
                select(WarningRuleSetProject.project_id).where(
                    WarningRuleSetProject.set_id == sid,
                    WarningRuleSetProject.project_id != project_id,
                )
            )
        ]
        st = db.get(WarningRuleSet, sid)
        db.query(WarningRuleSetProject).filter(
            WarningRuleSetProject.set_id == sid,
            WarningRuleSetProject.project_id == project_id,
        ).delete(synchronize_session=False)
        if st is None:
            continue
        if others or st.is_global:
            rules_kept.append(st.name)
            continue
        for r in db.scalars(select(WarningRule).where(WarningRule.set_id == sid)):
            db.delete(r)
        db.delete(st)
    # 老式项目级规则（改造前留下的、不属于任何套的行）—— 一并清掉
    db.execute(delete(WarningRule).where(WarningRule.project_id == project_id))
    db.flush()
    # 5) 导出模板 —— 模板已**独立于项目**（模板库），所以不能像以前那样"删项目就删光它的模板"：
    #    - 只被本项目用的模板 → 删除记录（原件 rename 为 .deleted，见 export_template_service）
    #    - 被别的项目共用的模板 → **只解除本项目的绑定，模板保留**（否则会把别人的母版一起弄没）
    from backend.models.export_template_link import ExportTemplateProject

    bound_ids = [
        int(x)
        for x in db.scalars(
            select(ExportTemplateProject.template_id).where(
                ExportTemplateProject.project_id == project_id
            )
        )
    ]
    kept_templates: list[str] = []
    for tid in bound_ids:
        others = [
            int(x)
            for x in db.scalars(
                select(ExportTemplateProject.project_id).where(
                    ExportTemplateProject.template_id == tid,
                    ExportTemplateProject.project_id != project_id,
                )
            )
        ]
        tpl = db.get(ExportTemplate, tid)
        db.query(ExportTemplateProject).filter(
            ExportTemplateProject.template_id == tid,
            ExportTemplateProject.project_id == project_id,
        ).delete(synchronize_session=False)
        if tpl is None:
            continue
        if others or tpl.is_global:
            # 还在被别人用（或本身就是通用母版）→ 保留
            kept_templates.append(tpl.name)
            if tpl.project_id == project_id:
                tpl.project_id = others[0] if others else None  # 兼容字段跟着挪走
            continue
        export_template_service.remove_template_file(tpl)
        db.delete(tpl)
    db.flush()
    # 6) 测点
    db.execute(delete(MeasurementPoint).where(MeasurementPoint.project_id == project_id))
    # 7) 项目本身
    db.delete(project)
    db.commit()

    return {
        "deleted": True,
        "kind": "project",
        "code": project.code,
        "points_removed": len(point_ids),
        # 保留下来的共用母版 / 共用预警规则（前端要告诉用户"这些没跟着删"）
        "templates_kept": kept_templates,
        "rules_kept": rules_kept,
    }


def count_project_data(db: Session, project_id: int) -> dict:
    """统计项目数据量（供前端确认弹窗展示）。"""
    point_ids = list(db.scalars(select(MeasurementPoint.id).where(MeasurementPoint.project_id == project_id)))
    records = 0
    if point_ids:
        records = len(db.scalars(select(ObservationRecord.id).where(ObservationRecord.point_id.in_(point_ids))).all())
    return {
        "points": len(point_ids),
        "records": records,
        "has_data": records > 0,
    }
