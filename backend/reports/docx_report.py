# -*- coding: utf-8 -*-
"""Word 日报生成（python-docx）—— 观测日报。

结构：项目头信息 → 本期观测汇总表 → 预警/异常清单 → 备注栏。
数据源与页面/Excel 完全一致（同一引擎结果），保证结论一致。
"""
from __future__ import annotations

from io import BytesIO
from datetime import date

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from backend.reports.export_format import (
    M_DECIMALS,
    RATE_DECIMALS,
    mm_to_m,
    round_mm,
    round_rate,
)


def _set_cn_font(run, size: int = 11, bold: bool = False):
    run.font.name = "宋体"
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def _cell_text(cell, text: str, bold: bool = False, align="center"):
    cell.text = ""
    p = cell.paragraphs[0]
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(str(text) if text is not None else "")
    _set_cn_font(run, bold=bold)


def _direction_label(sign_config: dict, series_type: str, delta_mm: float | None) -> str:
    """按项目 sign_config 给出方向语义。"""
    if delta_mm is None:
        return ""
    cfg = (sign_config or {}).get(series_type, {})
    positive_means = cfg.get("positive_means")
    if positive_means == "down":
        return "下沉" if delta_mm > 0 else ("隆起" if delta_mm < 0 else "无变化")
    if positive_means == "toward_pit":
        return "向基坑内" if delta_mm > 0 else ("向基坑外" if delta_mm < 0 else "无变化")
    # 行业惯例兜底
    return "下沉" if delta_mm > 0 else ("隆起" if delta_mm < 0 else "无变化")


def build_daily_docx(db, project_id: int, obs_date: date | None = None) -> tuple[BytesIO, str, dict]:
    """生成 Word 观测日报。返回 (BytesIO, filename, info)。"""
    from backend.models.point import series_types_of
    from backend.services import calc_service, comparison_service
    from backend.services.point_service import list_points
    from backend.services.project_service import get_project
    from backend.services.warning_service import ensure_control_rules

    project = get_project(db, project_id)
    points = list(list_points(db, project_id, include_inactive=False))
    ensure_control_rules(db, project_id, series_types_of(points))

    # 目标日期：缺省最近观测日
    from backend.repositories import observation_repo

    all_dates = sorted({r.obs_date for p in points for r in observation_repo.list_by_point(db, p.id, active_only=True)})
    if not all_dates:
        raise ValueError("该项目暂无观测数据，无法生成日报")
    target = obs_date or all_dates[-1]

    doc = Document()
    # 页面边距 A4
    for sec in doc.sections:
        sec.left_margin = Cm(2)
        sec.right_margin = Cm(2)

    # ---- 标题 ----
    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cn_font(h.add_run("基坑监测日报"), size=16, bold=True)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_cn_font(sub.add_run(f"{project.name}（{project.code}）"), size=12, bold=True)

    # ---- 项目信息 ----
    info_para = doc.add_paragraph()
    _set_cn_font(info_para.add_run(f"观测日期：{target.isoformat()}    地点：{project.location or '—'}"))

    # ---- 本期观测汇总表 ----
    doc.add_paragraph().add_run()
    p_title = doc.add_paragraph()
    _set_cn_font(p_title.add_run("一、本期观测成果汇总"), bold=True)

    rows_data: list[list] = []
    status_rows = calc_service.compute_project_warning_status(db, project_id)
    for row in status_rows:
        if row.get("obs_date") != target.isoformat():
            continue
        rows_data.append(row)

    # 若无当日行则取最新一期
    if not rows_data:
        rows_data = status_rows
        target = date.fromisoformat(rows_data[0]["obs_date"]) if rows_data and rows_data[0].get("obs_date") else target

    table = doc.add_table(rows=1, cols=7)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["测点", "观测项", "读数(m)", "本次变化(mm)", "累计变化(mm)", "速率(mm/d)", "预警状态"]
    for i, hd in enumerate(headers):
        _cell_text(table.rows[0].cells[i], hd, bold=True)
    for row in rows_data:
        cells = table.add_row().cells
        item = f"{'沉降' if row['series_type'] == 'settlement' else '水平位移'}/{row['component']}"
        wl = {"alarm": "报警", "control": "预警", "normal": "正常"}.get(row["warning_level"], "")
        val_m = mm_to_m(row["value_mm"])
        _cell_text(cells[0], row["point_no"])
        _cell_text(cells[1], item)
        _cell_text(cells[2], f"{val_m:.{M_DECIMALS}f}" if val_m is not None else "")
        # 纸上口径：毫米 1 位、速率 3 位（见 reports/export_format.py），
        # 否则 Word 里会印出 "0.4000000000000021mm" 这种数，和 Excel 报表也对不上。
        _cell_text(cells[3], round_mm(row["delta_mm"]) if row["delta_mm"] is not None else "")
        _cell_text(cells[4], round_mm(row["cumulative_mm"]) if row["cumulative_mm"] is not None else "")
        _cell_text(cells[5], f"{row['rate_mm_per_day']:.{RATE_DECIMALS}f}" if row["rate_mm_per_day"] is not None else "")
        _cell_text(cells[6], wl)

    # ---- 预警 / 异常清单 ----
    p2 = doc.add_paragraph()
    _set_cn_font(p2.add_run("二、预警与异常"), bold=True)
    alarms = [r for r in rows_data if r["warning_level"] == "alarm"]
    controls = [r for r in rows_data if r["warning_level"] == "control"]
    if alarms or controls:
        def _nums(r: dict) -> str:
            """列出这一期的三个数 —— **只列有值的**。

            触发语里出现「本次变化量达到报警值」时，光印累计和速率对不上号（用户看不到
            到底是哪个数超的）；反过来首期没有速率，硬印就成 "None mm/d"。
            """
            parts = []
            for label, v, unit in (
                ("本次", round_mm(r.get("delta_mm")), "mm"),
                ("累计", round_mm(r.get("cumulative_mm")), "mm"),
                ("速率", round_rate(r.get("rate_mm_per_day")), "mm/d"),
            ):
                if v is not None:
                    parts.append(f"{label} {v}{unit}")
            return "、".join(parts)

        def _clause(tag: str, r: dict) -> str:
            kind = "沉降" if r["series_type"] == "settlement" else "水平位移"
            return f"【{tag}】{r['point_no']} {kind}/{r['component']}：{_nums(r)} —— {'、'.join(r['triggers'])}"

        for r in alarms:
            pa = doc.add_paragraph()
            _set_cn_font(pa.add_run(_clause("报警", r)), size=11)
        for r in controls:
            pa = doc.add_paragraph()
            _set_cn_font(pa.add_run(_clause("预警", r)), size=11)
    else:
        pn = doc.add_paragraph()
        _set_cn_font(pn.add_run("本次观测各测点均处于正常状态，未触发预警。"))

    # ---- 逐期对比要点 ----
    p3 = doc.add_paragraph()
    _set_cn_font(p3.add_run("三、本期变化分析"), bold=True)
    sign_config = project.sign_config or {}
    for r in rows_data[:20]:
        pa = doc.add_paragraph()
        # 方向词按**纸上印的那个变化量**判：原值 0.04 收口径后印成 0.0，
        # 再写"下沉"就成了"变化 0.0mm（下沉）"这种自相矛盾的句子。
        delta = round_mm(r["delta_mm"])
        dir_label = _direction_label(sign_config, r["series_type"], delta)
        seg = f"{r['point_no']}（{'沉降' if r['series_type']=='settlement' else '水平位移'}/{r['component']}）：本次变化 {delta}mm（{dir_label}），累计变化 {round_mm(r['cumulative_mm'])}mm"
        _set_cn_font(pa.add_run(seg), size=11)

    # ---- 备注栏 ----
    p4 = doc.add_paragraph()
    _set_cn_font(p4.add_run("四、备注"), bold=True)
    p5 = doc.add_paragraph()
    _set_cn_font(p5.add_run("注：读值单位 m，变化量/累计/速率单位 mm（mm/d）；预警等级按项目配置的预警规则自动判断，控制值以设计文件为准。"), size=10)

    # ---- 签字区 ----
    doc.add_paragraph()
    sign = doc.add_paragraph()
    sign.add_run("监测人：                    ")
    sign.add_run("校核：                    ")
    _set_cn_font(sign.add_run("日期：                    "))

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    filename = f"{project.code}_基坑监测日报_{target.isoformat().replace('-', '.')}.docx"
    info = {
        "project_code": project.code,
        "project_name": project.name,
        "obs_date": target.isoformat(),
        "rows": len(rows_data),
        "alarm_count": len(alarms),
        "control_count": len(controls),
        "filename": filename,
    }
    return buf, filename, info
