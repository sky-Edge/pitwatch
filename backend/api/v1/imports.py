# -*- coding: utf-8 -*-
"""导入 API —— 九步流程的 HTTP 层。

- POST /projects/{project_id}/import/upload    上传文件 → 解析 → 校验 → 重复检查 → 返回预览
- POST /import/batches/{batch_id}/recheck      按用户指定的观测日期重新核对（不落库）
- POST /import/batches/{batch_id}/confirm      用户确认 → 正式落库
- GET  /import/batches                         批次列表（审计）
- POST /import/multi/upload                    一次上传多份台账 → 自动分到各项目（不落库）
- POST /import/multi/assign                    认不出的表由用户指定项目（不落库）
"""
from pathlib import Path

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
    MultiImportAssignRequest,
    MultiImportGroup,
    MultiImportPending,
    MultiImportPointList,
    MultiImportSheet,
    MultiImportUploadResponse,
    PointIssue,
)
from backend.services.multi_import_service import (
    SheetRoute,
    group_by_project,
    projects_for_routing,
    route_file,
    split_routes,
)
from backend.services.project_service import get_project

import_upload_router = APIRouter(tags=["import"])
batch_router = APIRouter(prefix="/import/batches", tags=["import"])
#: 跨项目导入（一次上传、自动分到各项目）。落库仍走 batch_router 的 confirm，不另开一条。
multi_router = APIRouter(prefix="/import/multi", tags=["import"])


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


def _make_batch(
    db: Session, project_id: int, filename: str, path, sheets: list[ParsedSheet]
) -> ImportBatch:
    """建一个「预览态」批次（单项目导入与跨项目导入共用，两条路结构必须一致）。

    `field_mapping` 里留两样东西：
    - `file_path`：用户改观测日期时要重解析这份文件，不存路径就没法重算；
    - `sheet_names`：**这个批次管哪几张表**。跨项目导入时一份文件会拆给好几个项目，
      不留白名单的话，改日期重新核对会把别的项目的表一起拉进来（见
      `importer.sheets_of_batch`）。单项目导入时白名单 = 全部表，行为不变。
    """
    batch = ImportBatch(
        project_id=project_id,
        filename=filename,
        status="previewed",
        field_mapping={
            "file_path": str(path),
            "sheet_names": [s.sheet_name for s in sheets],
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
    return batch


@import_upload_router.post("/projects/{project_id}/import/upload", response_model=ImportPreviewResponse)
def upload_and_preview(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Step 1-7：上传 → Sheet 识别 → 列识别 → 字段映射 → 预览 → 校验 → 重复检查。"""
    get_project(db, project_id)  # 404 校验
    path, sheets = _save_and_parse(file)
    batch = _make_batch(db, project_id, file.filename or path.name, path, sheets)
    return _preview_response(db, batch, sheets, file.filename or path.name)


def _save_and_parse(file: UploadFile):
    """存下上传的文件并解析出数据表（扩展名不对 / 解析不出表 → 4xx，不落到调用方头上）。"""
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
    return path, sheets


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
        # 没同步成的原因（台账控制值比系统报警值还大 → 倒挂，规则一条不改，但数据已入库）
        "control_sync_error": result.get("control_sync_error"),
    }


@batch_router.get("", response_model=list[ImportBatchRead])
def list_batches(db: Session = Depends(get_db)):
    """批次列表（审计）。"""
    return db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(50).all()


# ─────────────── 跨项目导入：一次上传、自动分到各项目 ───────────────
#
# 这里只做「认项目 + 出预览」，**一条数据都不落库**。落库仍然是用户点过的那个
# /import/batches/{id}/confirm —— 与项目内的 Excel 导入完全同一条路，
# 覆盖规则、跳过重复、控制值同步那些口径一个字都不改。


def _resolve_upload(upload_name: str):
    """按暂存文件名在**上传目录里**找回那份文件。

    ⚠ 前端回传的字符串绝不能直接拼路径：`Path(a) / "../../x"` 就能读到目录外去。
    这里先 `Path(...).name` 把目录成分全丢掉，再校验解析后的父目录就是上传目录。
    """
    from backend.core.config import settings

    name = Path(upload_name or "").name
    root = settings.upload_dir.resolve()
    p = (settings.upload_dir / name).resolve()
    if not name or p.parent != root or not p.is_file():
        raise HTTPException(status_code=404, detail="暂存的那份文件已经不在了，请重新上传")
    return p


def _group_payload(db: Session, project, filename: str, path, routes: list[SheetRoute]) -> MultiImportGroup:
    """一组（同项目、同文件）→ 建预览批次 + 出预览。"""
    sheets = [r.sheet for r in routes]
    batch = _make_batch(db, project.id, filename, path, sheets)
    return MultiImportGroup(
        batch_id=batch.id,
        project_id=project.id,
        project_code=project.code or "",
        project_name=project.name or "",
        project_archived=not bool(getattr(project, "is_active", True)),
        filename=filename,
        sheets=[MultiImportSheet(**r.to_dict()) for r in routes],
        preview=_preview_response(db, batch, sheets, filename),
        # 只是"相近"、或表里没写名称只按文件名认的 → 界面上要提醒核一眼
        needs_eyeball=any(r.close or r.from_filename for r in routes),
    )


@multi_router.post("/upload", response_model=MultiImportUploadResponse)
def multi_upload(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    """一次上传多份台账 → 逐张表认项目 → 按 (项目, 文件) 分组建预览批次。

    一份文件坏了不拖住其余的（记进 `errors` 点名报出来）—— 现场一次拖十几份是常态，
    因为其中一份格式不对就整批退回，用户只会再骂一次"点了没反应"。
    """
    projects = projects_for_routing(db)
    if not projects:
        raise HTTPException(
            status_code=422,
            detail="系统里还没有项目，没法自动分配 —— 请先建项目（或用项目列表的「批量建项目」）",
        )

    out = MultiImportUploadResponse()
    for f in files:
        original = f.filename or ""
        try:
            path, sheets = _save_and_parse(f)
        except HTTPException as e:
            out.errors.append(f"{original or '未命名文件'}：{e.detail}")
            continue
        routes = route_file(projects, original or path.name, sheets)
        routed, pending, point_lists = split_routes(routes)

        for pid, group in group_by_project(routed).items():
            project = next(p for p in projects if p.id == pid)
            out.groups.append(_group_payload(db, project, original or path.name, path, group))
        for r in pending:
            out.pending.append(
                MultiImportPending(
                    upload_name=path.name,
                    filename=original or path.name,
                    sheet=MultiImportSheet(**r.to_dict()),
                )
            )
        for r in point_lists:
            out.point_lists.append(
                MultiImportPointList(
                    filename=original or path.name,
                    sheet_name=r.sheet_name,
                    row_count=r.sheet.total_rows,
                )
            )
    return out


@multi_router.post("/assign", response_model=MultiImportGroup)
def multi_assign(payload: MultiImportAssignRequest, db: Session = Depends(get_db)):
    """把"认不出是哪个基坑"的表**由用户指定**项目 → 建批次 + 出预览。"""
    project = get_project(db, payload.project_id)
    path = _resolve_upload(payload.upload_name)
    try:
        sheets = importer.analyze_file(path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"文件解析失败：{e}") from e

    wanted = [n for n in (payload.sheet_names or []) if n]
    if not wanted:
        raise HTTPException(status_code=400, detail="没有指定要导入的数据表")
    chosen = [s for s in sheets if s.sheet_name in set(wanted)]
    missing = set(wanted) - {s.sheet_name for s in chosen}
    if missing:
        raise HTTPException(
            status_code=422,
            detail="这份文件里找不到这些数据表：" + "、".join(sorted(missing)) + "（文件被换过了？请重新上传）",
        )

    routes = [SheetRoute(sheet=s, project=project, how="你手动指定的项目", score=1.0) for s in chosen]
    return _group_payload(db, project, payload.filename or path.name, path, routes)
