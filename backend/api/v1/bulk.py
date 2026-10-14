# -*- coding: utf-8 -*-
"""项目批量操作 API —— 在主界面上"一次对多个项目做同一件事"。

用户 2026-10-11：
    「能不能把一些操作集中主界面上？每个项目都要调单独设置有点繁琐」

四件事（都是"进每个项目各点一遍"最烦的）：
- POST /bulk/projects/archive    批量归档 / 批量恢复（**不动任何观测数据**）
- POST /bulk/projects/rule-set   批量把几个项目绑到**同一套**预警规则（传 null = 解绑，回落到通用规则）
- POST /bulk/projects/template   批量把几个项目加进**同一份**母版的适用范围
- POST /bulk/projects/export     批量导出各项目的**最新一期**日报（ZIP 一次下载）

⚠ 三个原则：
1. **只加不覆盖**：批量套母版是"把项目加进适用清单"，不是"把别的母版踢掉"；
   一个项目的母版可以有好几份（下拉里选），这不冲突。
2. **规则是一个项目最多一套**，所以批量套规则确实是"改绑"，界面必须先说清会影响谁。
3. **部分失败要报出来**：某个项目没数据、母版原件缺失，都要在返回里点名，
   绝不静默少给一个文件（用户拿到 ZIP 才发现少了才是最坑的）。
"""
from __future__ import annotations

import io
import re
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.models.project import Project

bulk_router = APIRouter(prefix="/bulk/projects", tags=["bulk"])


class ProjectIds(BaseModel):
    """批量操作的通用入参。"""

    project_ids: list[int] = Field(default_factory=list, max_length=200)


class ArchivePayload(ProjectIds):
    #: True = 恢复在用；False = 归档
    active: bool = False


class RuleSetPayload(ProjectIds):
    #: 目标规则套；**null = 解绑**（这些项目改回吃通用规则）
    set_id: int | None = None


class TemplatePayload(ProjectIds):
    template_id: int


class ExportPayload(ProjectIds):
    #: 缺省 = 各项目走系统标准版式 / 各自上次用的母版
    template_id: int | None = None
    weather: str | None = None


def _load_projects(db: Session, ids: list[int]) -> list[Project]:
    """按传入顺序取出项目；不存在的直接在源头报错（不要"部分静默跳过"）。"""
    wanted = list(dict.fromkeys(int(i) for i in ids))
    if not wanted:
        raise HTTPException(status_code=422, detail="请先勾选要操作的项目")
    rows = {p.id: p for p in db.query(Project).filter(Project.id.in_(wanted)).all()}
    missing = [i for i in wanted if i not in rows]
    if missing:
        raise HTTPException(status_code=404, detail=f"项目不存在：{missing}")
    return [rows[i] for i in wanted]


@bulk_router.post("/archive")
def bulk_archive(payload: ArchivePayload, db: Session = Depends(get_db)):
    """批量归档 / 恢复。**只改在用状态，观测数据一条都不动**（归档本来就是这个语义）。"""
    projects = _load_projects(db, payload.project_ids)
    changed: list[str] = []
    already: list[str] = []
    for p in projects:
        if bool(p.is_active) == bool(payload.active):
            already.append(p.name)
            continue
        p.is_active = bool(payload.active)
        changed.append(p.name)
    db.commit()
    verb = "恢复" if payload.active else "归档"
    return {
        "changed": len(changed),
        "already": len(already),
        "names": changed,
        "already_names": already,
        "message": (
            f"已{verb} {len(changed)} 个项目"
            + (f"；另外 {len(already)} 个本来就是这个状态，没动" if already else "")
        ),
    }


@bulk_router.post("/rule-set")
def bulk_rule_set(payload: RuleSetPayload, db: Session = Depends(get_db)):
    """把这几个项目**改绑**到同一套预警规则（传 null = 解绑，改回吃通用规则）。

    规则是"一个项目最多一套"，所以这是**改绑**不是叠加 —— 返回里带上每个项目
    原来用的是哪套，界面才能如实告诉用户"这几个从哪套换到了哪套"。
    """
    from backend.services import warning_service

    projects = _load_projects(db, payload.project_ids)
    before: dict[int, str] = {}
    for p in projects:
        s = warning_service.rule_set_of_project(db, p.id)
        before[p.id] = s.name if s else "（没有，不判预警）"

    if payload.set_id is None:
        from backend.models.warning_rule_set import WarningRuleSetProject

        db.query(WarningRuleSetProject).filter(
            WarningRuleSetProject.project_id.in_([p.id for p in projects])
        ).delete(synchronize_session=False)
        db.commit()
        return {
            "changed": len(projects),
            "set_name": None,
            "before": before,
            "message": (
                f"已解除 {len(projects)} 个项目的规则绑定 —— 它们改回套用「通用规则」；"
                "若没有通用规则，将不再判预警"
            ),
        }

    s = warning_service.get_rule_set(db, payload.set_id)
    bound = warning_service.bound_project_ids(db, s.id)
    merged = sorted(set(bound) | {p.id for p in projects})
    warning_service.set_rule_set_projects(db, s.id, project_ids=merged)
    return {
        "changed": len(projects),
        "set_name": s.name,
        "before": before,
        "message": f"已把 {len(projects)} 个项目改绑到规则「{s.name}」，它现在一共作用于 {len(merged)} 个项目",
    }


@bulk_router.post("/template")
def bulk_template(payload: TemplatePayload, db: Session = Depends(get_db)):
    """把这几个项目加进**某一份母版的适用范围**（不是替换掉它们原有的母版）。

    一个项目可以有多份可用母版（导出时在下拉里选），所以批量套母版是"加上去"。
    母版若是「通用」，它本来就覆盖所有项目，这里只需要把项目加进去做个显式记录。
    """
    from backend.services import export_template_service

    projects = _load_projects(db, payload.project_ids)
    tpl = export_template_service.list_library(db)
    target = next((t for t in tpl if t.id == payload.template_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="母版不存在")
    if target.is_builtin:
        raise HTTPException(
            status_code=400,
            detail=f"「{target.name}」是系统内置版式，本来就所有项目都能用，不用绑定",
        )

    bound = export_template_service.bound_project_ids(db, target.id)
    merged = sorted(set(bound) | {p.id for p in projects})
    export_template_service.set_template_projects(
        db, target.id, project_ids=merged, is_global=bool(target.is_global)
    )
    return {
        "changed": len(projects),
        "template_name": target.name,
        "is_global": bool(target.is_global),
        "message": (
            f"已让 {len(projects)} 个项目可以用母版「{target.name}」，它现在作用于 {len(merged)} 个项目"
            + ("（通用）" if target.is_global else "")
        ),
    }


def _safe_filename(s: str) -> str:
    """去掉 Windows 文件名里非法的字符（项目名是从 Excel 里来的，什么字符都可能有）。"""
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s).strip() or "未命名"


def _own_template_id(db: Session, project_id: int) -> tuple[int | None, str]:
    """项目"自己那份"母版：第一份非内置、且原件还在的。

    为什么不默认走系统标准版式：现场每个基坑的日报都是用自己那份母版套的
    （表头、封面、概况页都不一样）。批量导出若悄悄换成标准版式，
    用户拿到手才发现"这批跟我平时发的不一样" —— 那还不如不批量。
    返回 (模板id, 说明里用的名字)；都没有时走系统标准版式。
    """
    from pathlib import Path

    from backend.services import export_template_service

    for t in export_template_service.list_templates(db, project_id):
        if t.is_builtin:
            continue
        if t.file_path and Path(t.file_path).exists():
            return t.id, t.name
    return None, "系统标准版式"


@bulk_router.post("/export")
def bulk_export(payload: ExportPayload, db: Session = Depends(get_db)):
    """批量导出各项目的**最新一期**日报，打包成一个 ZIP。

    为什么是"最新一期"：现场最常见的诉求是"这几个基坑这个礼拜的日报一起给我"，
    而各基坑的观测节奏并不齐 —— 硬要同一个日期，多半有几个是空的。
    要按同一个日期导，请用各项目导出页的批量导出。

    没有观测数据的项目**跳过但点名**（写进《说明.txt》），绝不静默少一个文件。

    `template_id` 不传（默认）→ **每个项目用它自己那份母版**（见 `_own_template_id`），
    这样批量出来的和平时单项目导的长得一样；每个文件用的是哪份母版也写进《说明.txt》。
    """
    from datetime import date as _date

    from backend.api.v1.reports import _build_excel_one
    from backend.services.baseline_service import project_record_dates

    projects = _load_projects(db, payload.project_ids)
    zip_buf = io.BytesIO()
    done: list[str] = []
    skipped: list[tuple[str, str]] = []
    used_names: dict[str, int] = {}

    # 外层用 STORED：xlsx 本身已是 zip，再 DEFLATE 一次几乎没收益还要多花 CPU
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_STORED) as zf:
        for p in projects:
            try:
                dates = project_record_dates(db, p.id).get("dates_with_values") or []
                if not dates:
                    skipped.append((f"{p.code} {p.name}", "还没有观测数据"))
                    continue
                latest = dates[-1]
                obs = _date.fromisoformat(latest)
                if payload.template_id is not None:
                    tid, tpl_name = payload.template_id, f"指定母版 #{payload.template_id}"
                else:
                    tid, tpl_name = _own_template_id(db, p.id)
                buf, _fname, _info = _build_excel_one(db, p.id, tid, obs, payload.weather)
                base = _safe_filename(f"{p.code}_{p.name}_{latest}")
                # 同名（编号或名字重复）时加序号，不覆盖
                n = used_names.get(base, 0) + 1
                used_names[base] = n
                name = f"{base}.xlsx" if n == 1 else f"{base}({n}).xlsx"
                zf.writestr(name, buf.getvalue())
                done.append(f"  {name}\n      用母版：{tpl_name}")
            except Exception as e:  # noqa: BLE001
                # 单项目失败不中断其余（与"逐期批量导出"同一策略）
                skipped.append((f"{p.code} {p.name}", str(getattr(e, "detail", None) or e)))

        lines = ["本次批量导出（每个项目取最新一期）", ""]
        lines += done
        if skipped:
            lines += ["", "下面这些项目没能导出：", ""]
            lines += [f"  · {who}：{why}" for who, why in skipped]
        zf.writestr("说明.txt", "\n".join(lines) + "\n")

    if not done:
        raise HTTPException(
            status_code=422,
            detail="所选项目一个都没导出成功：" + "；".join(f"{w}（{r}）" for w, r in skipped[:3]),
        )

    zip_buf.seek(0)
    from urllib.parse import quote

    fname = f"批量日报_最新一期_共{len(done)}个项目.zip"
    return Response(
        content=zip_buf.getvalue(),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
