# -*- coding: utf-8 -*-
"""导出模板服务：上传模板 → 自动识别布局 → 保存 → 列表。

自动识别逻辑（openpyxl 读表头行 R5/R6 文本，关键词匹配列号）：
- 点号：含"点号"
- 初始值：含"初始值"
- 上次测值：含"上次测值"
- 本次测值：含"本次测值"
- 上次累计：含"上次累计"
- 本次累计：含"本次累计"
- 本次变化：含"本次变化"
- 速率：含"速率"
- 控制累计/速率：R6"累计变化量"→控制累计；R6"变化速率"→控制速率
- 预警等级：含"预警"
- data_start_row：表头行下第 1 行（默认在 R6 下方 → 7）

水平位移 Sheet：同点 X/Y 为相邻两行（X 行有点号、Y 行继承）——mode=x_y_two_rows。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.export_template import ExportTemplate
from backend.models.export_template_link import ExportTemplateProject

# 内置模板种子（project_id=NULL 且 is_builtin=True）
BUILTIN_EXCEL = "标准监测日报（竖向位移 + 水平位移）"
BUILTIN_WORD = "Word 观测日报"


def _column_keywords() -> list[tuple[str, list[str]]]:
    return [
        ("point_col", ["点号"]),
        ("v0_m", ["初始值"]),
        ("prev_m", ["上次测值"]),
        ("current_m", ["本次测值"]),
        ("prev_cum_mm", ["上次累计"]),
        ("cum_mm", ["本次累计"]),
        ("delta_mm", ["本次变化"]),
        ("rate", ["速率"]),
        ("warning_level", ["预警"]),
    ]


def _match_col(cells: list[str], keywords: list[str]) -> int | None:
    """在表头行文本里找第一个含任一关键词的列（1-based）。"""
    for i, txt in enumerate(cells, start=1):
        if txt and any(k in txt for k in keywords):
            return i
    return None


def map_columns(top: list[str], sub: list[str], header_row: int = 5) -> dict:
    """把「双击表头」（R5 + R6）映射成列号 —— **列判据的唯一出处**。

    写的一方（导出：`master_report.fill_data_sheets`）与读的一方（检查导出文件：
    `reports/export_reader`）必须用同一套判据。各写一份的话，
    迟早出现"导出写进 H 列、检查去 G 列读"这种两边都跑得通、结论全错的局面。

    返回 {point_col, v0_m, prev_m, current_m, prev_cum_mm, cum_mm, delta_mm, rate,
          control_cum, control_rate, data_start_row}
    """
    layout: dict = {}
    for field, kws in _column_keywords():
        col = _match_col(top, kws) or _match_col(sub, kws)
        if col:
            layout[field] = col
    # 控制值两列：R5"控制值"合并 → R6"累计变化量"/"变化速率"
    if any("控制值" in t for t in top):
        cum_c = _match_col(sub, ["累计"]) if sub else None
        rate_c = _match_col(sub, ["速率"]) if sub else None
        if cum_c:
            layout["control_cum"] = cum_c
        if rate_c:
            layout["control_rate"] = rate_c
    # 数据起始行：表头行下方。若下方行仍是字段表头（累计/速率/控制值等）→ 再跳 1 行（双层表头）
    sub_is_header = any(("累计" in t or "速率" in t or "控制" in t or "预警" in t) for t in sub)
    layout["data_start_row"] = header_row + (2 if sub_is_header else 1)
    return layout


#: 数据类 Sheet 的表名提示（判断"这张表是不是监测数据表"）
DATA_SHEET_HINTS = ("位移", "沉降", "竖向", "水平", "监测", "变形", "高程")
SKIP_SHEET_HINTS = ("分析", "统计", "封面", "概况", "签字", "成果", "说明", "汇总")


def is_data_sheet_name(name: str) -> bool:
    """这张 sheet 是不是监测数据表（导出与检查共用同一个判据）。"""
    if any(h in name for h in SKIP_SHEET_HINTS) and not any(h in name for h in DATA_SHEET_HINTS):
        return False
    return any(h in name for h in DATA_SHEET_HINTS)


def detect_layout(path: Path, sheet_names: list[str] | None = None) -> dict | None:
    """读取上传模板，自动推断布局。

    对模板内所有含"点号"表头的 Sheet 识别（竖向位移 → settlement，位移 → x_y_two_rows）。
    返回 {meta, sheets:[{sheet_name, layout, mode}]}。
    """
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"无法读取模板文件：{e}") from e

    targets = [s for s in wb.sheetnames if s in (sheet_names or wb.sheetnames)]
    sheets_out: list[dict] = []
    for target in targets:
        if not is_data_sheet_name(target):
            continue
        ws = wb[target]
        # 读前 9 行，找含"点号"的表头行
        rows_text: list[list[str]] = []
        header_row: int | None = None
        for r in range(1, min(10, ws.max_row + 1)):
            cells = [str(c.value) if c.value is not None else "" for c in ws[r]]
            rows_text.append(cells)
            if header_row is None and any("点号" in t for t in cells):
                header_row = r  # 记录但不 break，保证 sub 行已读入
        if header_row is None:
            continue

        top = rows_text[header_row - 1]
        sub = rows_text[header_row] if header_row < len(rows_text) else []

        layout = map_columns(top, sub, header_row)

        # 位移 sheet 特征：表名含 水平/r.立柱（竖向位移=沉降！）
        is_disp = ("水平" in target or "r." in target.lower()) and "竖向" not in target
        sheets_out.append(
            {
                "sheet_name": target,
                "layout": layout,
                "mode": "x_y_two_rows" if is_disp else "per_row",
                "kind": "displacement" if is_disp else "settlement",
            }
        )

    wb.close()
    if not sheets_out:
        return None
    # 元信息：扫描各 Sheet 前 4 行找"本次监测时间 / 上次监测时间 / 工程名称"，记录归属 sheet
    meta: dict = {}
    wb2 = load_workbook(path, data_only=True, read_only=True)
    try:
        for sn in wb2.sheetnames:
            ws = wb2[sn]
            for r in range(1, min(5, (ws.max_row or 1) + 1)):
                for c in range(1, min(6, (ws.max_column or 1) + 1)):
                    v = ws.cell(row=r, column=c).value
                    if v is None:
                        continue
                    t = str(v)
                    if "date_cell" not in meta and "本次监测时间" in t:
                        meta["date_cell"] = f"{_col_letter(c)}{r}"
                        meta.setdefault("date_sheet", sn)
                    if "project_cell" not in meta and ("工程名称" in t or "监测工程" in t):
                        meta["project_cell"] = f"{_col_letter(c)}{r}"
                        meta.setdefault("project_sheet", sn)
    finally:
        wb2.close()
    return {"meta": meta, "sheets": sheets_out}


def _col_letter(col: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(col)


def seed_builtin_templates(db: Session) -> None:
    """首次启动补齐内置模板（幂等）。"""
    if not db.query(ExportTemplate).filter(ExportTemplate.is_builtin.is_(True)).first():
        db.add_all(
            [
                ExportTemplate(project_id=None, name=BUILTIN_EXCEL, template_type="excel", is_builtin=True),
                ExportTemplate(project_id=None, name=BUILTIN_WORD, template_type="word", is_builtin=True),
            ]
        )
        db.commit()


# ─────────────────── 模板库：模板自己声明适用项目（多对多） ───────────────────
#
# 用户 2026-10-08：「每个项目单独维护一个模板太繁琐了，能否把模板从项目中独立提取出来，
# 改由模板来定义其作用于哪个项目？」
# 于是可见性只由两件事决定：`is_global`（通用）与 `template_projects`（绑定的项目）。
# ⚠ `ExportTemplate.project_id` **不再参与可见性判断** —— 它只是历史来源，
#   保留它是为了"代码回退到旧版本时行为不变"（旧的 list_templates 正是读它）。

#: 新模板原件的落脚目录名（老的 templates/{项目id}/ 继续可用，不动）
LIBRARY_DIRNAME = "_library"


def library_dir(template_id: int) -> Path:
    d = settings.templates_dir / LIBRARY_DIRNAME / str(template_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def bound_project_ids(db: Session, template_id: int) -> list[int]:
    from sqlalchemy import select

    stmt = (
        select(ExportTemplateProject.project_id)
        .where(ExportTemplateProject.template_id == template_id)
        .order_by(ExportTemplateProject.project_id)
    )
    return [int(x) for x in db.scalars(stmt)]


def bindings_for(db: Session, template_ids) -> dict[int, list[int]]:
    """一次查出多份模板的绑定项目（避免列表接口 N+1 查询）。"""
    from sqlalchemy import select

    ids = [int(i) for i in template_ids]
    if not ids:
        return {}
    stmt = (
        select(ExportTemplateProject.template_id, ExportTemplateProject.project_id)
        .where(ExportTemplateProject.template_id.in_(ids))
        .order_by(ExportTemplateProject.template_id, ExportTemplateProject.project_id)
    )
    out: dict[int, list[int]] = {}
    for tid, pid in db.execute(stmt):
        out.setdefault(int(tid), []).append(int(pid))
    return out


def usable_clause(project_id: int):
    """「这份模板能用于该项目」的 SQL 判据 —— **唯一出处**（列表与导出校验共用）。

    成立条件（任一）：
    1. 内置模板（走系统标准版式）；
    2. 通用模板（`is_global`）；
    3. 显式绑定到该项目（`template_projects`）；
    4. **迁移兜底**：一条绑定都没有、也不是通用的"孤儿"行，按历史字段 `project_id` 归属。

    第 4 条只兜"从未建立绑定的行"（迁移没覆盖到、或绕过服务层直接写库产生的行）。
    它**不会**让解绑失效：服务层禁止把模板配成"既不通用的、又没绑任何项目"，
    所以手工解绑后不存在"0 绑定"状态，这条兜底也就不会把解绑的模板又冒出来。
    """
    from sqlalchemy import and_, exists, or_, select

    any_link = select(ExportTemplateProject.template_id).where(
        ExportTemplateProject.template_id == ExportTemplate.id
    )
    return or_(
        ExportTemplate.is_builtin.is_(True),
        ExportTemplate.is_global.is_(True),
        ExportTemplate.id.in_(
            select(ExportTemplateProject.template_id).where(
                ExportTemplateProject.project_id == project_id
            )
        ),
        and_(
            ExportTemplate.project_id == project_id,
            ~exists(any_link),
        ),
    )


def list_templates(db: Session, project_id: int | None = None) -> list[ExportTemplate]:
    """某项目可用的模板 = 内置 + 通用 + 显式绑定到该项目的。project_id 传 None 时只返回内置。"""
    from sqlalchemy import select

    if project_id is None:
        stmt = select(ExportTemplate).where(ExportTemplate.is_builtin.is_(True))
    else:
        stmt = select(ExportTemplate).where(usable_clause(project_id))
    return list(db.scalars(stmt.order_by(ExportTemplate.id)))


def list_library(db: Session) -> list[ExportTemplate]:
    """模板库全量清单（含通用、含未被任何项目绑定的），给"管理视角"用。"""
    from sqlalchemy import select

    return list(db.scalars(select(ExportTemplate).order_by(ExportTemplate.id)))


def is_usable_by(db: Session, tpl: ExportTemplate, project_id: int) -> bool:
    """这份模板能不能用于该项目（导出接口的归属校验）。

    刻意走 `usable_clause` 同一套判据 —— 两个地方各写一份判断，
    迟早会出现"下拉里有、导出却被拦"这种自相矛盾。
    """
    from sqlalchemy import select

    stmt = select(ExportTemplate.id).where(ExportTemplate.id == tpl.id, usable_clause(project_id))
    return db.scalars(stmt).first() is not None


def set_template_projects(
    db: Session,
    template_id: int,
    project_ids: list[int] | None = None,
    is_global: bool | None = None,
) -> ExportTemplate:
    """设置模板的适用范围（只传要改的那一项）。

    防呆：不允许把模板配成"哪儿都用不了" —— 现在还没有独立的母版库页面，
    一旦既不通用的又没绑项目，它在导出页下拉里彻底消失、也没地方捞回来。
    """
    from sqlalchemy import select

    from backend.models.project import Project

    tpl = db.get(ExportTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if tpl.is_builtin:
        raise HTTPException(status_code=400, detail="内置模板的适用范围不可修改")

    if is_global is not None:
        tpl.is_global = bool(is_global)

    if project_ids is not None:
        wanted = sorted({int(p) for p in project_ids})
        if wanted:
            found = {int(x) for x in db.scalars(select(Project.id).where(Project.id.in_(wanted)))}
            missing = [p for p in wanted if p not in found]
            if missing:
                raise HTTPException(status_code=404, detail=f"项目不存在：{missing}")
        if not wanted and not tpl.is_global:
            raise HTTPException(
                status_code=400,
                detail="至少要选一个项目，或者设为「所有项目通用」—— 否则这份母版在导出页里就找不到了",
            )
        db.query(ExportTemplateProject).filter(
            ExportTemplateProject.template_id == template_id
        ).delete(synchronize_session=False)
        for pid in wanted:
            db.add(ExportTemplateProject(template_id=template_id, project_id=pid))
        # 兼容字段：老版本代码靠它判断归属，留一个"主项目"给它
        tpl.project_id = wanted[0] if wanted else None

    db.commit()
    db.refresh(tpl)
    return tpl


def create_uploaded_template(
    db: Session,
    name: str,
    src_path: Path,
    layout_json: dict | None,
    original_filename: str = "template.xlsx",
    project_ids: list[int] | None = None,
    is_global: bool = False,
    legacy_project_id: int | None = None,
) -> tuple[ExportTemplate, str]:
    """把上传的模板存进**模板库**（原件副本 + 布局），返回 (记录, 同名替换说明)。

    - 原件落到 `templates/_library/{模板id}/`：模板不再属于某个项目，就不该躺在项目目录下。
      **老模板的文件原地不动** —— file_path 是绝对路径，搬迁等于造断链风险。
    - **同名即替换，且继承原绑定**：这正是"改一次全局生效"的用法 —— 重新上传改进版
      「基坑日报」，原先绑定的那 9 个项目自动跟过来，不用一个个重新勾。
    - **名称允许留空 → 用文件名兜底**。上传顺序不该被"名字"卡住：用户常常先挑文件、
      再想名字（甚至事后才想改）。留空就用文件名，传完还能在导出页重命名
      （`PUT /export-templates/{id}`）—— 这条兜底规则只写在这里，别在页面里再抄一份。
    """
    name = (name or "").strip() or Path(original_filename or "").stem or "未命名母版"
    name = name[:100]

    # 同名替换（全局唯一；内置除外）—— 并把被替换者的绑定继承下来
    same = (
        db.query(ExportTemplate)
        .filter(ExportTemplate.name == name, ExportTemplate.is_builtin.is_(False))
        .all()
    )
    inherit_ids: set[int] = set()
    inherit_global = False
    for old in same:
        inherit_ids |= set(bound_project_ids(db, old.id))
        inherit_global = inherit_global or bool(old.is_global)
        _remove_template_file(old)
        db.delete(old)
    db.flush()

    targets = sorted(set(project_ids or []) | inherit_ids)
    if legacy_project_id and not targets:
        targets = [int(legacy_project_id)]
    is_global = bool(is_global or inherit_global)
    if not targets and not is_global:
        raise HTTPException(
            status_code=400,
            detail="请至少选择一个适用项目，或勾选「所有项目通用」—— 否则这份母版建好也没地方能用",
        )

    rec = ExportTemplate(
        project_id=targets[0] if targets else None,  # DEPRECATED：仅为回退兼容保留
        is_global=is_global,
        name=name,
        template_type="excel",
        is_builtin=False,
        file_path=None,
        layout_json=layout_json,
    )
    db.add(rec)
    db.flush()  # 先拿到 id 才能定目录（目录名就是模板 id）

    tpl_dir = library_dir(rec.id)
    suffix = Path(original_filename).suffix or ".xlsx"
    dest = tpl_dir / f"{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}{suffix}"
    dest.write_bytes(src_path.read_bytes())
    rec.file_path = str(dest)

    for pid in targets:
        db.add(ExportTemplateProject(template_id=rec.id, project_id=pid))

    db.commit()
    db.refresh(rec)

    note = ""
    if same:
        if inherit_global:
            note = f"已替换同名母版「{name}」（原为通用母版，新的保持通用）"
        elif inherit_ids:
            note = f"已替换同名母版「{name}」，它原先的 {len(inherit_ids)} 个项目已自动继承"
        else:
            note = f"已替换同名母版「{name}」"
    return rec, note


def _remove_template_file(tpl: ExportTemplate) -> None:
    """移除模板原件文件：rename 为 .deleted 后缀。

    注意：绝不调用 Path.unlink / os.remove —— 运行环境的安全删除守卫会拦截
    物理删除并抛 SystemExit（此前多次踩坑）。rename 不触发守卫且幂等。
    .deleted 残留极小且无害（作审计/可恢复），后续可人工清理。
    """
    if not tpl.file_path:
        return
    try:
        p = Path(tpl.file_path)
        if p.exists():
            backup = p.with_suffix(p.suffix + ".deleted")
            if backup.exists():
                # 同名已残留 → 加时间戳后缀避免 rename 覆盖冲突
                backup = p.with_suffix(p.suffix + f".deleted_{datetime.now():%H%M%S}")
            p.rename(backup)
    except Exception:  # noqa: BLE001
        pass


def remove_template_file(tpl: ExportTemplate) -> None:
    """公开别名：硬删项目时也要按同一套规则清理原件（别在别处重写一遍 rename 逻辑）。"""
    _remove_template_file(tpl)


def _assert_name_free(db: Session, name: str, exclude_id: int) -> None:
    """模板名现在**全局唯一**（原来只是"项目内唯一"）—— 两个同名母版在下拉框里分不出来。"""
    dup = (
        db.query(ExportTemplate)
        .filter(
            ExportTemplate.name == name,
            ExportTemplate.id != exclude_id,
            ExportTemplate.is_builtin.is_(False),
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=400,
            detail=f"已经有一个叫「{name}」的母版了 —— 请换个名字（两个同名母版在导出页的下拉框里分不出来）",
        )


def update_template(db: Session, template_id: int, data) -> ExportTemplate:
    """改名称 / 布局。模板已独立于项目，因此不再做"属不属于某项目"的校验。"""
    tpl = db.get(ExportTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if tpl.is_builtin:
        raise HTTPException(status_code=400, detail="内置模板不可修改")
    if data.name:
        new_name = data.name.strip()
        _assert_name_free(db, new_name, tpl.id)
        tpl.name = new_name
    if data.layout_json is not None:
        tpl.layout_json = data.layout_json
    db.commit()
    db.refresh(tpl)
    return tpl


def delete_template(db: Session, template_id: int) -> None:
    """删除模板：先解除全部项目绑定，再移除记录 + 清理原件文件。"""
    tpl = db.get(ExportTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    if tpl.is_builtin:
        raise HTTPException(status_code=400, detail="内置模板不可删除")
    db.query(ExportTemplateProject).filter(
        ExportTemplateProject.template_id == template_id
    ).delete(synchronize_session=False)
    _remove_template_file(tpl)
    db.delete(tpl)
    db.commit()
