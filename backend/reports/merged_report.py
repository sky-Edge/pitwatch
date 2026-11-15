# -*- coding: utf-8 -*-
"""合并报表：把**多个项目**合出同一份整卷日报。

【场景】南北两侧钢栈桥同一天测、装订成一份册子；几个相邻基坑同理。
一份册子里封面 / 签字页 / 概况 / 分析 / 布点图**只有一份**，数据表各占一张纸。

【口径 —— 宁可报错，不出一份错报表】
- 一次报表 = 同一天的一次观测。指定的日期只要有一个项目没有数据就**报错**，
  并说清"哪个项目最近一期是哪天"，绝不拿旧数据凑一页。
- 不给日期时取**所有项目都有数据的最近一天**（不是各自最新一期）。
- 期次 / 封面 / 签字 / 概况 / 分析 以主项目为准；每张数据表的"本次/上次监测时间"
  按它自己的项目写。
- 数据仍各存各的项目，这里只负责装订；不写任何观测记录。

【和单项目导出的关系】单项目导出（`build_master_report`）一行没改，
合并走的是新的 `build_merged_report`；两者共用同一套填充函数，口径不会分叉。
"""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from backend.models.export_template import ExportTemplate
from backend.reports.export_cleanup import clear_stale_stat_tables
from backend.reports.master_report import (
    _cn_period,
    _collect_period_cells,
    _ensure_text_blocks,
    _kinds_for_report,
    _period_text,
    _resolve_cell,
    _write_date_cell,
    _write_text_cell,
    fill_data_sheets,
    fill_raw_data_blocks,
    write_sheet_monitor_dates,
)
from backend.services.project_service import get_project


def _dates_with_values(db: Session, project_id: int) -> list[date]:
    """该项目**有有效读数**的观测日（缺测日不算，否则会当成"那天测过"）。"""
    from backend.repositories import observation_repo
    from backend.services.point_service import list_points

    out: set[date] = set()
    for p in list_points(db, project_id, include_inactive=False):
        for r in observation_repo.list_by_point(db, p.id, active_only=True):
            if r.value_mm is not None:
                out.add(r.obs_date)
    return sorted(out)


def _set_cover_project_name(wb, name: str) -> int:
    """只改封面「工程名称：」右边那一格。

    **不做全簿替换**：两张数据表的「工点名称」各自写着北侧/南侧，
    整簿替换会把它们一起改成册子名，反而丢失"这张表是哪一侧"的信息。
    """
    from openpyxl.cell.cell import MergedCell

    pat = re.compile(r"^\s*工程名称\s*[:：]\s*$")
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell) or not isinstance(cell.value, str):
                    continue
                if not pat.match(cell.value):
                    continue
                for step in range(1, 4):
                    tgt = ws.cell(row=cell.row, column=cell.column + step)
                    if isinstance(tgt, MergedCell):
                        continue
                    tgt.value = name
                    return 1
    return 0


def plan_group(db: Session, group) -> tuple[ExportTemplate, dict, dict, list[dict]]:
    """校验组合并整理出"每张表 ← 哪个项目"的清单。

    返回 (母版, layout, {表名: 表配置}, [{project, scfg, dates, sheet_name}])。
    任何一处配错都当场抛 ValueError（API 转 422），不留到出报表时才炸。
    """
    tpl = db.get(ExportTemplate, group.template_id)
    if tpl is None:
        raise ValueError("报表组合指向的母版不存在")
    if not tpl.file_path or not Path(tpl.file_path).exists():
        raise ValueError(f"母版「{tpl.name}」原件缺失，请重新上传")

    layout = tpl.layout_json or {}
    sheet_cfgs = {s.get("sheet_name"): s for s in (layout.get("sheets") or [])}
    if not (sheet_cfgs and layout.get("meta")):
        raise ValueError(f"母版「{tpl.name}」没有识别出数据表布局，不能用于合并报表")

    items = sorted(group.items, key=lambda i: (i.sort_order or 0, i.id))
    if not items:
        raise ValueError("这个报表组合还没有绑定项目")

    plans: list[dict] = []
    for it in items:
        project = get_project(db, it.project_id)
        scfg = sheet_cfgs.get(it.sheet_name)
        if scfg is None:
            raise ValueError(f"母版「{tpl.name}」里没有数据表「{it.sheet_name}」，请检查组合配置")
        dates = _dates_with_values(db, it.project_id)
        if not dates:
            raise ValueError(f"项目「{project.name}」还没有观测数据，无法参与合并报表")
        plans.append({"sheet_name": it.sheet_name, "project": project, "scfg": scfg, "dates": dates})

    bound = {p["sheet_name"] for p in plans}
    unbound = [s for s in sheet_cfgs if s not in bound]
    if unbound:
        raise ValueError(f"母版里的数据表 {'、'.join(unbound)} 还没有绑定项目，合并出来会是空白页")
    return tpl, layout, sheet_cfgs, plans


def pick_date(plans: list[dict], obs_date: date | None) -> date:
    """定本期日期：一次报表 = 同一天的一次观测。"""
    if obs_date is not None:
        lack = [p for p in plans if obs_date not in p["dates"]]
        if lack:
            detail = "；".join(
                f"{p['project'].name} 最近一期 {p['dates'][-1].isoformat()}" for p in lack
            )
            raise ValueError(
                f"所选日期 {obs_date.isoformat()} 在这些项目上没有数据："
                f"{'、'.join(p['project'].name for p in lack)}（{detail}）"
                "—— 合并报表要求各项目同一天都有数据"
            )
        return obs_date

    common = set(plans[0]["dates"])
    for p in plans[1:]:
        common &= set(p["dates"])
    if not common:
        raise ValueError(
            "这些项目没有共同的观测日，无法合成一期："
            + "、".join(f"{p['project'].name}（{len(p['dates'])} 期）" for p in plans)
        )
    return max(common)


def resolve_merged_date(db: Session, group, obs_date: date | None = None) -> date:
    """只算本期日期（导出接口先拿它取天气，避免重复校验）。"""
    _tpl, _layout, _cfgs, plans = plan_group(db, group)
    return pick_date(plans, obs_date)


def build_merged_report(
    db: Session,
    group,
    obs_date: date | None = None,
    weather: str = "",
    instrument: dict | None = None,
) -> tuple[BytesIO, str, dict]:
    """按「报表组合」生成一份合并整卷日报，返回 (bytes, 文件名, 摘要)。"""
    tpl, layout, sheet_cfgs, plans = plan_group(db, group)
    target = pick_date(plans, obs_date)

    # 控制值必须有出处（与单项目导出一条口径）：缺规则就拦住，不照抄母版上的旧限值
    from backend.services.warning_service import ensure_control_rules

    for p in plans:
        ensure_control_rules(db, p["project"].id, {p["scfg"].get("kind") or "settlement"})

    # ── 主项目定期次（封面/签字只有一份期次，以它为准） ──
    main_plan = next((p for p in plans if p["project"].id == group.main_project_id), plans[0])
    main_project = main_plan["project"]
    period_no = main_plan["dates"].index(target)
    is_initial = period_no == 0

    wb = load_workbook(tpl.file_path, data_only=False)

    # ── 封面工程名称 / 期次 / 封面与签字日期 ──
    display_name = (getattr(group, "display_name", None) or "").strip()
    if display_name:
        _set_cover_project_name(wb, display_name)

    meta = layout.get("meta") or {}
    period_cells = _collect_period_cells(wb, layout)
    sample = None
    if period_cells:
        ws0, r0, c0 = _resolve_cell(wb, period_cells[0])
        if ws0 is not None:
            sample = ws0.cell(row=r0, column=c0).value
    period_text = _period_text(period_no, sample)
    for ref in period_cells:
        _write_text_cell(wb, ref, period_text)
    _write_date_cell(wb, meta.get("cover_date_cell"), target)
    _write_date_cell(wb, meta.get("sign_date_cell"), target)

    # ── 逐张数据表：换它自己的日期 + 填**它自己那个项目**的数 ──
    fill_summary: dict = {"template_points": {}, "added": {}, "shifted": {}}
    raw_summary: dict = {"added": {}, "missing": {}}
    ordered_plans: list[dict] = []
    for sn, _scfg in sheet_cfgs.items():
        plan = next((p for p in plans if p["sheet_name"] == sn), None)
        if plan is None:
            continue
        ordered_plans.append(plan)
        write_sheet_monitor_dates(db, plan["project"].id, wb, sn, target, is_initial)
        sub_layout = {"meta": meta, "sheets": [plan["scfg"]]}
        s = fill_data_sheets(
            db, plan["project"].id, wb, sub_layout, target, weather=weather, is_initial=is_initial
        )
        for key in ("template_points", "added", "shifted"):
            fill_summary[key].update(s.get(key) or {})
        raw = fill_raw_data_blocks(
            db, plan["project"].id, wb, sub_layout, target, is_initial=is_initial
        )
        for key in ("added", "missing"):
            raw_summary[key].update((raw or {}).get(key) or {})

    # ── 统计表清旧期（母版里是别的工程的点位） ──
    cleared_stats = clear_stale_stat_tables(wb)

    # ── 文字页：按**合并后**的本期数据重写（点号带项目名，避免两侧重名分不清） ──
    text_blocks = _ensure_text_blocks(db, tpl, layout)
    text_summary: dict = {}
    if text_blocks:
        from backend.reports.template_excel import _series_rows
        from backend.reports.text_blocks import GenContext, apply_text_blocks

        rows_all: list[dict] = []
        kinds: list[str] = []
        for plan in ordered_plans:
            rows = _series_rows(db, plan["project"].id, target)
            label = (plan["project"].name or "").strip()
            for r in rows:
                row = dict(r)
                row["label"] = label
                rows_all.append(row)
            for k in _kinds_for_report({"sheets": [plan["scfg"]]}, rows):
                if k not in kinds:
                    kinds.append(k)
        text_summary = apply_text_blocks(
            wb,
            text_blocks,
            GenContext(
                project_name=display_name or main_project.name,
                obs_date=target,
                period_text=("初始值" if is_initial else f"第{_cn_period(period_no)}期"),
                is_initial=is_initial,
                rows=rows_all,
                kinds=kinds,
            ),
        )

    # ── 仪器 + 电子签名（整册一份） ──
    from backend.services import app_settings_service, instrument_service, signature_service

    instrument_service.apply_to_workbook(wb, instrument)
    _sig = app_settings_service.get_settings(db)
    signature_service.stamp_workbook(
        wb,
        size_px=_sig.get("signature_size_px"),
        offset_x=_sig.get("signature_offset_x") or 0,
        offset_y=_sig.get("signature_offset_y") or 0,
    )

    buf = BytesIO()
    wb.save(buf)
    wb.close()
    buf.seek(0)

    title = display_name or main_project.name
    filename = f"{title}{period_text}{target.isoformat().replace('-', '.')}.xlsx"
    info = {
        "group": getattr(group, "name", ""),
        "project_name": title,
        "obs_date": target.isoformat(),
        "period": f"第{_cn_period(period_no)}期" if period_no > 0 else "初始值",
        "filename": filename,
        "weather": weather,
        "sheets": [
            {"sheet_name": p["sheet_name"], "project": p["project"].name, "obs_date": target.isoformat()}
            for p in ordered_plans
        ],
        "template_points": fill_summary.get("template_points") or {},
        "added_points": fill_summary.get("added") or {},
        "shifted_rows": fill_summary.get("shifted") or {},
        "missing_raw_points": raw_summary.get("missing") or {},
        "added_raw_points": raw_summary.get("added") or {},
        "cleared_stats": cleared_stats,
        "text_generated": text_summary.get("generated") or {},
        "text_cleared": text_summary.get("cleared") or {},
        "text_overflow": text_summary.get("overflow") or {},
    }
    return buf, filename, info
