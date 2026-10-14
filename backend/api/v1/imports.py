# -*- coding: utf-8 -*-
"""导入 API —— 九步流程的 HTTP 层。

- POST /projects/{project_id}/import/upload    上传文件 → 解析 → 校验 → 重复检查 → 返回预览
- POST /import/batches/{batch_id}/recheck      按用户指定的观测日期重新核对（不落库）
- POST /import/batches/{batch_id}/confirm      用户确认 → 正式落库
- GET  /import/batches                         批次列表（审计）
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.importers import importer
from backend.importers.parser import ParsedSheet
from backend.models.import_batch import ImportBatch
from backend.schemas.import_schema import (
    ControlValueSuggest,
    ImportBatchRead,
    ImportConfirmRequest,
    ImportPreviewResponse,
    ImportPreviewRow,
    ImportRecheckRequest,
    PointIssue,
)
from backend.services.project_service import get_project

import_upload_router = APIRouter(tags=["import"])
batch_router = APIRouter(prefix="/import/batches", tags=["import"])


def _sheets_payload(
    sheets: list[ParsedSheet], originals: dict[str, str | None] | None = None
) -> list[dict]:
    """数据表清单（前端据此渲染"逐表改观测日期"）。

    带 `original_obs_date` = 表里原本写的是哪天；用户改过之后 `obs_date` 是改后的值，
    两个值一起给前端，才能显示"表里写的是 X"，用户才核对得出来自己改对没有。

    带 `kind` = 这张表是「逐期观测台账」还是「点位清单」：点位清单（点号+坐标/高程）
    走的其实是「批量建项目」那条路，前端据此提示用户"是不是要建新项目"而不是继续导。
    """
    out: list[dict] = []
    for s in sheets:
        current = s.obs_date.isoformat() if s.obs_date else None
        original = originals.get(s.sheet_name) if originals else current
        out.append(
            {
                "sheet_name": s.sheet_name,
                "series_type": s.series_type,
                "kind": s.kind,
                "obs_date": current,
                "original_obs_date": original,
                "row_count": s.total_rows,
                "column_map": s.column_map,
                "needs_date": s.obs_date is None and s.total_rows > 0,
            }
        )
    return out


def _preview_response(
    db: Session,
    batch: ImportBatch,
    sheets: list[ParsedSheet],
    filename: str,
    originals: dict[str, str | None] | None = None,
) -> ImportPreviewResponse:
    """跑校验 + 组装响应（upload 与 recheck 共用，保证两条入口结构完全一致）。"""
    preview = importer.preview_batch(db, batch, sheets)

    # 台账里的「控制值」记进批次的 field_mapping：用户确认导入时若勾了"同步到预警规则"，
    # 直接取这里（重解析同一份文件没必要；recheck 也不会改变控制值）。
    suggest = preview.get("control_suggest") or []
    if suggest:
        batch.field_mapping = {
            **(batch.field_mapping or {}),
            "control_values": {
                s["indicator"]: {
                    "control_cum_mm": s["control_cum_mm"],
                    "control_rate_mm_d": s["control_rate_mm_d"],
                }
                for s in suggest
            },
        }
        db.add(batch)
        db.commit()

    return ImportPreviewResponse(
        batch_id=batch.id,
        filename=filename,
        sheets=_sheets_payload(sheets, originals),
        valid_rows=[ImportPreviewRow(**r) for r in preview["valid_rows"]],
        override_rows=[ImportPreviewRow(**r) for r in preview.get("override_rows", [])],
        conflict_rows=[ImportPreviewRow(**r) for r in preview["conflict_rows"]],
        error_rows=[ImportPreviewRow(**r) for r in preview["error_rows"]],
        no_date_rows=[ImportPreviewRow(**r) for r in preview.get("no_date_rows", [])],
        point_issues=[PointIssue(**r) for r in preview.get("point_issues", [])],
        control_suggest=[ControlValueSuggest(**r) for r in suggest],
    )


@import_upload_router.post("/projects/{project_id}/import/upload", response_model=ImportPreviewResponse)
def upload_and_preview(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Step 1-7：上传 → Sheet 识别 → 列识别 → 字段映射 → 预览 → 校验 → 重复检查。"""
    get_project(db, project_id)  # 404 校验
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xlsm / .xls 文件")
    raw = file.file.read()
    path = importer.save_upload(raw, file.filename)
    try:
        sheets = importer.analyze_file(path)
    except Exception as e:  # noqa: BLE001 —— 解析失败给用户友好提示
        raise HTTPException(status_code=422, detail=f"文件解析失败：{e}") from e

    if not sheets:
        raise HTTPException(status_code=422, detail="未识别到数据 Sheet（竖向位移/水平位移），请检查文件模板")

    # 建批次（预览态）。field_mapping 里同时留下**原始文件路径**：
    # 用户改观测日期时要重解析这份文件，不存路径就没法重算。
    batch = ImportBatch(
        project_id=project_id,
        filename=file.filename,
        status="previewed",
        field_mapping={
            "file_path": str(path),
            "sheets": [
                {
                    "sheet_name": s.sheet_name,
                    "series_type": s.series_type,
                    "obs_date": s.obs_date.isoformat() if s.obs_date else None,
                    "column_map": {k: v for k, v in s.column_map.items()},
                    "row_count": s.total_rows,
                }
                for s in sheets
            ],
        },
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    return _preview_response(db, batch, sheets, file.filename)


@batch_router.post("/{batch_id}/recheck", response_model=ImportPreviewResponse)
def recheck_batch(batch_id: int, payload: ImportRecheckRequest, db: Session = Depends(get_db)):
    """按用户指定的观测日期重新核对（不落库）。

    为什么必须重算：换一个日期，"同一天已有没有记录"就变了 ——
    改到已有记录的日期要变成冲突行（跳过），把原冲突的日期改开又应该变成可导入行。
    沿用旧预览会让用户看到错的条数，导入时才发现少了几条。
    """
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.status != "previewed":
        raise HTTPException(status_code=400, detail=f"批次状态为 {batch.status}，不可再修改日期")

    overrides = {d.sheet_name: d.obs_date for d in payload.dates}
    try:
        sheets, originals = importer.recheck_with_dates(batch, overrides)
    except FileNotFoundError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not sheets:
        raise HTTPException(status_code=422, detail="按新日期重新解析后没有识别到数据 Sheet")

    db.add(batch)
    db.commit()
    return _preview_response(db, batch, sheets, batch.filename, originals)


@batch_router.post("/{batch_id}/confirm", response_model=dict)
def confirm_import(batch_id: int, payload: ImportConfirmRequest, db: Session = Depends(get_db)):
    """Step 8-9：用户确认（可排除部分行）→ 正式落库。

    valid_rows 由前端回传（用户可剔除不想导入的行）；服务端仅导入这批数据。
    同日已有「导入/手工录入」记录 → 跳过不覆盖；同日已有「系统自动生成」记录
    → 以用户导入数据为准替换（旧记录标记 superseded 留痕）。
    """
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.status != "previewed":
        raise HTTPException(status_code=400, detail=f"批次状态为 {batch.status}，不可导入")

    rows = [r.model_dump() for r in payload.valid_rows]
    actions = [a.model_dump() for a in payload.point_actions]
    result = importer.confirm_batch(
        db, batch, rows, point_actions=actions or None,
        sync_control_values=payload.sync_control_values,
    )
    created_pts = 0
    if actions:
        # 重新统计动作结果（confirm_batch 已内部提交，简单计数自动建点）
        created_pts = len([a for a in actions if a.get("auto_create")])
    return {
        "batch_id": batch.id,
        "imported_rows": result["imported"],
        "replaced_generated": result["replaced_generated"],
        "skipped_no_date": result.get("skipped_no_date", 0),
        "skipped_dup_existing": result.get("skipped_dup", 0),
        "points_auto_created": created_pts,
        "status": batch.status,
        # 同步进预警规则的变更（用户勾了才有；前端据此回显"规则改成了什么"）
        "control_changes": result.get("control_changes", []),
    }


@batch_router.get("", response_model=list[ImportBatchRead])
def list_batches(db: Session = Depends(get_db)):
    """批次列表（审计）。"""
    return db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(50).all()
