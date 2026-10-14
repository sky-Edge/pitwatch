# -*- coding: utf-8 -*-
"""导出模板 API —— **模板库**形态（模板不再归属单一项目）。

模板自己声明适用范围（`is_global` 通用 / `template_projects` 绑定若干项目），
所以「改一次表头」只要重传一份同名母版，原先绑定的项目全部自动跟过来。

- GET    /export-templates                        模板库清单（管理视角，带适用项目）
- POST   /export-templates/upload                 上传母版（可带 project_ids / is_global）
- PUT    /export-templates/{tid}                  改名称 / 布局
- PUT    /export-templates/{tid}/projects         设置适用项目（多选 + 通用开关）
- DELETE /export-templates/{tid}                  删除模板
- GET    /export-templates/{tid}/file             下载模板原件（可选）
- GET    /projects/{pid}/export-templates         该项目**可用**的模板（导出页下拉用）
- POST   /projects/{pid}/export-templates/upload  兼容入口（内部转调上传，自动绑定本项目）
"""
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.db import get_db
from backend.models.export_template import ExportTemplate
from backend.schemas.export_template import ExportTemplateRead, TemplateScopeUpdate, TemplateUpdate
from backend.services import export_template_service
from backend.services.project_service import get_project, project_name_map

tpl_router = APIRouter(prefix="/export-templates", tags=["export-template"])
upload_router = APIRouter(prefix="/projects/{project_id}/export-templates", tags=["export-template"])


def _to_read(db: Session, tpl: ExportTemplate, names: dict[int, str] | None = None) -> ExportTemplateRead:
    """补上"适用范围"两个派生字段（模型里没有这两个属性，不能靠 from_attributes）。"""
    pids = export_template_service.bound_project_ids(db, tpl.id)
    name_of = names if names is not None else project_name_map(db)
    return ExportTemplateRead(
        id=tpl.id,
        project_id=tpl.project_id,
        name=tpl.name,
        template_type=tpl.template_type,
        is_builtin=tpl.is_builtin,
        is_global=bool(tpl.is_global),
        project_ids=pids,
        project_names=[name_of.get(p, f"项目#{p}") for p in pids],
        file_path=tpl.file_path,
        layout_json=tpl.layout_json,
        file_ok=(not tpl.file_path) or Path(tpl.file_path).exists(),
        created_at=tpl.created_at,
    )


@upload_router.get("", response_model=list[ExportTemplateRead])
def list_templates(project_id: int, db: Session = Depends(get_db)):
    """项目可用模板：内置 + 通用 + 绑定到本项目的。附带 file_ok（原件是否存在）。"""
    get_project(db, project_id)
    items = export_template_service.list_templates(db, project_id)
    names = project_name_map(db)
    return [_to_read(db, t, names) for t in items]


@tpl_router.get("", response_model=list[ExportTemplateRead])
def list_library(db: Session = Depends(get_db)):
    """模板库全量清单（含通用、含当前没绑任何项目的）——管理视角用。"""
    items = export_template_service.list_library(db)
    names = project_name_map(db)
    return [_to_read(db, t, names) for t in items]


def _do_upload(
    db: Session,
    *,
    file: UploadFile,
    name: str,
    project_ids: list[int],
    is_global: bool,
    legacy_project_id: int | None,
) -> dict:
    """上传公共流程（新入口与项目内兼容入口共用，保证两条路行为完全一致）。"""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx/.xlsm 模板文件")

    # 保存到临时再读取识别（母版学习模式：识别变量槽）
    # 落在一个共享的 _incoming 目录（模板不再归属项目，没必要按项目分目录）；
    # 文件名带时间戳 + uuid —— 同秒并发传同名文件也不会互相覆盖。
    tmp_dir = settings.templates_dir / "_incoming"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"_upload_tmp_{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}_{file.filename}"
    tmp.write_bytes(file.file.read())

    from backend.reports.master_learn import learn_master_layout

    try:
        layout = learn_master_layout(tmp)
    except Exception as e:  # noqa: BLE001
        layout = None
        message_ = f"母版学习失败：{e}"
    else:
        message_ = (
            f"模板已保存，已识别 {len((layout or {}).get('sheets', []))} 个数据 Sheet 与封面/期次变量槽"
            if layout
            else "模板已保存（未识别到数据表结构）"
        )

    try:
        tpl, replaced_note = export_template_service.create_uploaded_template(
            db,
            name,
            tmp,
            layout,
            original_filename=file.filename,
            project_ids=project_ids,
            is_global=is_global,
            legacy_project_id=legacy_project_id,
        )
    finally:
        # 无论成功与否都清理上传临时文件（rename 为 .deleted，避免孤儿堆积；
        # 不 unlink —— 安全删除守卫会拦截物理删除）
        try:
            if tmp.exists():
                dest = tmp.with_suffix(tmp.suffix + ".deleted")
                if dest.exists():
                    dest = tmp.with_suffix(tmp.suffix + f".deleted_{datetime.now():%H%M%S}")
                tmp.rename(dest)
        except Exception:  # noqa: BLE001
            pass

    if replaced_note:
        message_ = f"{message_}；{replaced_note}"

    bound = export_template_service.bound_project_ids(db, tpl.id)
    return {
        "id": tpl.id,
        "name": tpl.name,
        "detected_layout": layout,
        "master_mode": bool(layout and layout.get("sheets")),
        "is_global": bool(tpl.is_global),
        "project_ids": bound,
        "message": message_,
    }


def _parse_ids(raw: str | None) -> list[int]:
    out: list[int] = []
    for part in (raw or "").replace("，", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                raise HTTPException(status_code=422, detail=f"适用项目编号不是数字：{part}") from None
    return sorted(set(out))


@tpl_router.post("/upload")
def upload_to_library(
    file: UploadFile = File(...),
    name: str = Form("", max_length=100),
    project_ids: str = Form("", description="适用项目 id，逗号分隔；留空且非通用 → 报错"),
    is_global: bool = Form(False, description="所有项目通用"),
    db: Session = Depends(get_db),
):
    """把母版传进模板库，并指定它作用于哪些项目（默认建议带上当前项目）。"""
    return _do_upload(
        db,
        file=file,
        name=name,
        project_ids=_parse_ids(project_ids),
        is_global=is_global,
        legacy_project_id=None,
    )


@upload_router.post("/upload")
def upload_template(
    project_id: int,
    file: UploadFile = File(...),
    name: str = Form("", max_length=100),
    db: Session = Depends(get_db),
):
    """**兼容入口**：在项目里上传母版 → 自动绑定本项目。

    模板独立化之后模板不再归属项目，但"我就想给当前这个项目传一份"是最常用的操作，
    保留这条路（内部就是给模板库传一份 + 绑定本项目），前端与老测试都不用改。

    `name` **可留空**（留空用文件名）—— 传文件与起名字没有先后要求，
    名字事后也能改（`PUT /export-templates/{id}`）。
    """
    get_project(db, project_id)
    return _do_upload(
        db,
        file=file,
        name=name,
        project_ids=[project_id],
        is_global=False,
        legacy_project_id=project_id,
    )


@tpl_router.put("/{template_id}", response_model=ExportTemplateRead)
def update_template(template_id: int, payload: TemplateUpdate, db: Session = Depends(get_db)):
    """更新模板（仅自定义模板；内置不可改）。模板已独立于项目，不再校验归属。"""
    tpl = export_template_service.update_template(db, template_id, payload)
    return _to_read(db, tpl)


@tpl_router.put("/{template_id}/projects", response_model=ExportTemplateRead)
def set_template_projects(
    template_id: int, payload: TemplateScopeUpdate, db: Session = Depends(get_db)
):
    """设置这份模板作用于哪些项目（多选 + 通用开关）。

    用户痛点：「每个项目单独维护一个模板太繁琐」—— 在这里把一份母版同时绑给几个项目，
    或者直接设成通用，就不用每个项目各传一遍了。
    """
    tpl = export_template_service.set_template_projects(
        db, template_id, project_ids=payload.project_ids, is_global=payload.is_global
    )
    return _to_read(db, tpl)


@tpl_router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """删除模板（内置不可删）。删除时一并解除它与各项目的绑定。"""
    export_template_service.delete_template(db, template_id)
    return {"deleted": True}


@tpl_router.get("/{template_id}/file")
def download_template_file(template_id: int, db: Session = Depends(get_db)):
    """下载模板原件（用于预览）。"""
    tpl = db.get(ExportTemplate, template_id)
    if tpl is None or not tpl.file_path or not Path(tpl.file_path).exists():
        raise HTTPException(status_code=404, detail="模板原件不存在")
    filename = Path(tpl.file_path).name
    return FileResponse(
        tpl.file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
