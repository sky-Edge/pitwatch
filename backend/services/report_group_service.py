# -*- coding: utf-8 -*-
"""合并报表组合的增删改查 + 校验。

校验只做"这份组合能不能出报表"这一件事，宁可建的时候就退回，
也不要在导出时才发现某张表绑了个空项目。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models.export_template import ExportTemplate
from backend.models.report_group import ReportGroup, ReportGroupItem
from backend.services.project_service import get_project


class GroupError(ValueError):
    """组合配置不合法（API 层转 400）。"""


def _template_sheets(db: Session, template_id: int) -> dict[str, dict]:
    tpl = db.get(ExportTemplate, template_id)
    if tpl is None:
        raise GroupError("母版不存在")
    sheets = (tpl.layout_json or {}).get("sheets") or []
    if not sheets:
        raise GroupError(f"母版「{tpl.name}」没有识别出数据表，不能用于合并报表")
    return {s.get("sheet_name"): s for s in sheets}


def _apply_items(db: Session, group: ReportGroup, items) -> None:
    """整体替换组合里的条目，并做全套校验。"""
    sheets = _template_sheets(db, group.template_id)
    seen_sheets: set[str] = set()
    seen_projects: set[int] = set()
    new_items: list[ReportGroupItem] = []
    for idx, it in enumerate(items):
        if it.sheet_name not in sheets:
            raise GroupError(f"母版里没有数据表「{it.sheet_name}」")
        if it.sheet_name in seen_sheets:
            raise GroupError(f"数据表「{it.sheet_name}」被绑了两次")
        if it.project_id in seen_projects:
            raise GroupError("同一个项目只能出现一次（否则一张报表里会有两页同源数据）")
        get_project(db, it.project_id)  # 不存在 → 404
        seen_sheets.add(it.sheet_name)
        seen_projects.add(it.project_id)
        new_items.append(
            ReportGroupItem(
                project_id=it.project_id,
                sheet_name=it.sheet_name,
                sort_order=it.sort_order if it.sort_order is not None else idx,
            )
        )
    if not new_items:
        raise GroupError("至少要绑一张数据表")
    if group.main_project_id not in seen_projects:
        raise GroupError("主项目必须在组合里 —— 封面、签字页、期次都以它为准")
    missing = [s for s in sheets if s not in seen_sheets]
    if missing:
        raise GroupError(
            "这些数据表还没绑定项目，合并出来会是空白页：" + "、".join(missing)
        )
    group.items = new_items


def list_groups(db: Session) -> list[ReportGroup]:
    return db.query(ReportGroup).order_by(ReportGroup.id).all()


def get_group(db: Session, group_id: int) -> ReportGroup:
    g = db.get(ReportGroup, group_id)
    if g is None:
        raise GroupError("报表组合不存在")
    return g


def create_group(db: Session, data) -> ReportGroup:
    group = ReportGroup(
        name=(data.name or "").strip(),
        display_name=(data.display_name or "").strip() or None,
        template_id=data.template_id,
        main_project_id=data.main_project_id,
    )
    if not group.name:
        raise GroupError("组合名不能为空")
    group.items = []
    _apply_items(db, group, data.items)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def update_group(db: Session, group_id: int, data) -> ReportGroup:
    group = get_group(db, group_id)
    if data.name is not None:
        if not data.name.strip():
            raise GroupError("组合名不能为空")
        group.name = data.name.strip()
    if data.display_name is not None:
        group.display_name = data.display_name.strip() or None
    if data.template_id is not None and data.template_id != group.template_id:
        group.template_id = data.template_id
        # 换了母版，旧的表名多半对不上 —— 强制重配，避免"看起来没变、导出空白"
        if data.items is None:
            raise GroupError("换了母版要重新指定每张数据表对应的项目")
    if data.main_project_id is not None:
        group.main_project_id = data.main_project_id
    if data.items is not None:
        _apply_items(db, group, data.items)
    elif data.main_project_id is not None and group.main_project_id not in {
        i.project_id for i in group.items
    }:
        raise GroupError("主项目必须在组合里")
    db.commit()
    db.refresh(group)
    return group


def delete_group(db: Session, group_id: int) -> None:
    group = get_group(db, group_id)
    db.delete(group)
    db.commit()


def serialize(db: Session, group: ReportGroup) -> dict:
    """给前端回显：带上项目名、母版名。"""
    tpl = db.get(ExportTemplate, group.template_id)
    try:
        main_name = get_project(db, group.main_project_id).name
    except Exception:  # noqa: BLE001 —— 主项目被删/归档时不要把列表接口带崩
        main_name = None
    items = []
    for it in sorted(group.items, key=lambda i: (i.sort_order or 0, i.id)):
        try:
            pname = get_project(db, it.project_id).name
        except Exception:  # noqa: BLE001
            pname = None
        items.append(
            {
                "project_id": it.project_id,
                "project_name": pname,
                "sheet_name": it.sheet_name,
                "sort_order": it.sort_order,
            }
        )
    return {
        "id": group.id,
        "name": group.name,
        "display_name": group.display_name,
        "template_id": group.template_id,
        "main_project_id": group.main_project_id,
        "is_active": group.is_active,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "items": items,
        "template_name": tpl.name if tpl else None,
        "main_project_name": main_name,
    }


def template_sheet_options(db: Session, template_id: int) -> list[dict]:
    """这份母版里有哪些数据表（给"表 ↔ 项目"下拉用）。"""
    sheets = _template_sheets(db, template_id)
    return [
        {"sheet_name": name, "kind": cfg.get("kind") or "settlement"}
        for name, cfg in sheets.items()
    ]
