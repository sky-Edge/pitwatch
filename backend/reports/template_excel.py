# -*- coding: utf-8 -*-
"""按用户上传模板（底模 + layout 定位）生成单期日报。

流程：读模板原件副本 → 定位数据 Sheet 与列 → 从 Calc/Chart 数据源取各测点各分量
该 obs_date 的 初始值/上次/本次/累计/变化/速率/预警 → 逐行写入模板 → 另存新文件。

只写 layout 中配置的列，模板其余区域（封面/签字/页眉）原样保留 —— 100% 模板外观。
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from backend.models.export_template import ExportTemplate
from backend.repositories import observation_repo
from backend.reports.export_cleanup import clear_stale_stat_tables
from backend.reports.export_format import autofit_numeric_columns, paper_values, round_m
from backend.reports.export_options import EXPORT_SHOW_WARNING_LEVEL
from backend.services.warning_service import ensure_control_rules, report_control_values
from backend.services import calc_service
from backend.services.point_service import list_points
from backend.services.project_service import get_project
from backend.warnings.engine import LEVEL_ALARM, LEVEL_CONTROL


def _series_rows(db: Session, project_id: int, obs_date: date) -> list[dict]:
    """该日所有测点分量：读值(原始 mm→显示 m) + 引擎结果（累计/变化/速率/预警）。

    从 calc 序列里推导 target 行及其前一有效期（prev 行），确保 C/D/E/F 列取值正确。
    """
    out: list[dict] = []
    points = [p for p in list_points(db, project_id, include_inactive=False)]
    for p in points:
        sts = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in sts:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            for comp in comps:
                rows = calc_service.compute_point_series(db, p, st, comp)
                if not rows:
                    continue
                # 找 target 行（该日有效）
                target_idx = next(
                    (i for i, r in enumerate(rows) if r["obs_date"] == obs_date.isoformat() and not r["is_missing"]),
                    None,
                )
                if target_idx is None:
                    continue
                target = rows[target_idx]
                # 前一有效期行（同分量）
                prev = None
                for r in reversed(rows[:target_idx]):
                    if not r["is_missing"]:
                        prev = r
                        break
                v0_any = p.initial_value_mm
                if v0_any is None:
                    first = next((r for r in rows if not r["is_missing"]), None)
                    v0_any = first["value_mm"] if first else None
                # 读数与派生量一律走 `paper_values` 收到纸上口径：报表上
                # (本次测值 − 上次测值)×1000 必须恒等于「本次变化量」那一格。
                out.append(
                    {
                        "point_no": p.point_no,
                        "series_type": st,
                        "component": comp,
                        "obs_date": obs_date,
                        **paper_values(
                            v0_any,
                            prev["value_mm"] if prev else None,
                            target["value_mm"],
                            target.get("skipped_days"),
                        ),
                        "warning_level": target.get("warning_level"),
                    }
                )
    return out


#: 公开别名：「文件检查」要拿系统里的数去和导出的报表比，
#: 必须走**导出时同一套取数**（`_series_rows`），否则两边口径不同、查出来的全是"假不一致"。
series_rows_for_date = _series_rows


def _warning_label(level: str | None) -> str:
    if level == LEVEL_ALARM:
        return "报警"
    if level == LEVEL_CONTROL:
        return "预警"
    return ""


def _set_cell(ws, row: int, col: int | None, value) -> None:
    """写单元格；**value=None 也生效（清空）**。

    ⚠ 不能写成 `ws.cell(row=..., column=..., value=value)`：openpyxl 对 `value=None`
    是"不设置"而不是"清空"，母版自带的上一期数值/预警文字会原样留在导出文件里
    （实测：把预警列写成 None 后，导出的 K 列仍然是母版的"预警"）。
    """
    if not col:
        return
    ws.cell(row=row, column=col).value = value


def build_from_template(db: Session, project_id: int, tpl: ExportTemplate, obs_date: date | None = None) -> tuple[BytesIO, str, dict]:
    """以模板原件为底模生成单期日报。返回 (bytes, filename, info)。"""
    project = get_project(db, project_id)
    if not tpl.file_path or not Path(tpl.file_path).exists():
        raise ValueError("模板原件缺失，请重新上传")
    layout = tpl.layout_json or {}
    sheets_cfg = layout.get("sheets") or []
    # 与母版套打、标准版式同一个口径：项目没配「控制值」就拦住，不拿底模上印的旧数顶（用户 2026-10-14）。
    ensure_control_rules(db, project_id, {s.get("kind") for s in sheets_cfg if s.get("kind")})

    # 目标日期：默认取"数据最完整的最近观测日"（避免取到只录了部分点的零散日期）
    points = list_points(db, project_id, include_inactive=False)
    all_dates = sorted({r.obs_date for p in points for r in observation_repo.list_by_point(db, p.id, active_only=True)})
    if not all_dates:
        raise ValueError("该项目暂无观测数据")
    if obs_date is None:
        # 逐日统计有效读数数，选最近且行数 >= 峰值 80% 的日期（完整观测期）
        from collections import Counter

        counts = Counter()
        for p in points:
            for r in observation_repo.list_by_point(db, p.id, active_only=True):
                if r.value_mm is not None:
                    counts[r.obs_date] += 1
        if counts:
            peak = max(counts.values())
            target = max((d for d, c in counts.items() if c >= peak * 0.8))
        else:
            target = all_dates[-1]
    else:
        target = obs_date

    data = _series_rows(db, project_id, target)

    wb = load_workbook(tpl.file_path, data_only=False)
    written = 0
    written_rows: dict[str, list[int]] = {}
    meta = layout.get("meta") or {}

    # 工程名称统一：母版常是拿**别的基坑**那期另存的（用户 2026-10-08 报障：
    # 项目叫「十号基坑」导出却写着「十一号」）。与母版套打路径共用同一套改写逻辑。
    from backend.reports.master_report import _sync_project_name

    _sync_project_name(wb, project.name)
    if meta.get("date_cell"):
        ws0 = wb[wb.sheetnames[0]]
        _set_cell(ws0, 1, 1, None)  # no-op guard

    for scfg in sheets_cfg:
        if scfg.get("sheet_name") not in wb.sheetnames:
            continue
        ws = wb[scfg["sheet_name"]]
        f = scfg.get("layout") or {}
        start = f.get("data_start_row") or 7
        mode = scfg.get("mode") or "per_row"
        sheet_kind = scfg.get("kind") or ("displacement" if "水平" in scfg.get("sheet_name", "") else "settlement")

        # 每 Sheet 填其类型对应数据
        subset = [r for r in data if r["series_type"] == sheet_kind]

        # 控制值：与另两条导出路径同一个出处（预警规则的 control 级）
        ctrl_cum, ctrl_rate = report_control_values(db, project_id, sheet_kind)

        row = start
        rows_here: list[int] = []
        # 按测点分组：位移时 X 行 = 测点行，Y 行紧随其后（模板惯例）
        by_point: dict[str, list[dict]] = {}
        for r in subset:
            by_point.setdefault(r["point_no"], []).append(r)
        for point_no, comps in by_point.items():
            # 该测点的 X（或沉降 value）主行
            main = next((c for c in comps if c["component"] == ("value" if sheet_kind == "settlement" else "X")), comps[0])
            _write_point_row(ws, row, f, point_no, main, ctrl_cum, ctrl_rate)
            written += 1
            rows_here.append(row)
            row += 1
            if mode == "x_y_two_rows" and sheet_kind == "displacement":
                y = next((c for c in comps if c["component"] == "Y"), None)
                if y:
                    # Y 行通常点号留空（继承上行）
                    _write_point_row(ws, row, f, "", y, ctrl_cum, ctrl_rate)
                    written += 1
                    rows_here.append(row)
                    row += 1
        if rows_here:
            written_rows[scfg["sheet_name"]] = rows_here

    # 元信息填充：更新归属 sheet 的"本次监测时间/工程名称"（保留前缀）
    def _meta_sheet(name_key: str, default_cell: str | None = None):
        sn = meta.get(f"{name_key}_sheet")
        cell_ref = meta.get(name_key) or default_cell
        if sn and cell_ref and sn in wb.sheetnames:
            return wb[sn], cell_ref
        if cell_ref:
            return wb[wb.sheetnames[0]], cell_ref
        return None, None

    ws_meta, date_cell_ref = _meta_sheet("date")
    if ws_meta is not None and date_cell_ref:
        cell = ws_meta.cell(row=int(date_cell_ref[1:]), column=_col_of(date_cell_ref))
        if cell.value and "本次监测时间" in str(cell.value):
            prefix = str(cell.value).split("：")[0] + "："
            cell.value = f"{prefix}{target.isoformat()}"
        else:
            cell.value = target.isoformat()
    ws_meta, proj_cell_ref = _meta_sheet("project")
    if ws_meta is not None and proj_cell_ref:
        cell = ws_meta.cell(row=int(proj_cell_ref[1:]), column=_col_of(proj_cell_ref))
        prefix = str(cell.value).split("：")[0] + "：" if cell.value and "：" in str(cell.value) else ""
        cell.value = f"{prefix}{project.name}"

    # 兜底：把每个 Sheet 前 4 行中形如"本次监测时间：xxxx"的文本统一刷成目标日期
    for sn in wb.sheetnames:
        ws_s = wb[sn]
        for r_ in range(1, min(5, (ws_s.max_row or 1) + 1)):
            for c_ in range(1, min(6, (ws_s.max_column or 1) + 1)):
                v_ = ws_s.cell(row=r_, column=c_).value
                if v_ and "本次监测时间" in str(v_):
                    prefix_ = str(v_).split("：")[0] + "："
                    ws_s.cell(row=r_, column=c_).value = f"{prefix_}{target.isoformat()}"

    # 母版副本里自带的「××统计表」旧数据（别的基坑的点位与预警等级）→ 清掉
    cleared_stats = clear_stale_stat_tables(wb)

    # 列宽按内容收拾一眼（只加宽；表头有自动换行不参与）——与母版套打口径一致
    for sn, rows_here in written_rows.items():
        autofit_numeric_columns(wb[sn], [5, 6, *rows_here], list(range(1, 13)))

    buf = BytesIO()
    wb.save(buf)
    wb.close()
    buf.seek(0)
    filename = f"{project.code}_日报_{target.isoformat().replace('-', '.')}（{tpl.name}）.xlsx"
    info = {
        "project_code": project.code,
        "obs_date": target.isoformat(),
        "template": tpl.name,
        "rows_written": written,
        "cleared_stats": cleared_stats,
        "filename": filename,
    }
    return buf, filename, info


def _col_of(cell: str) -> int | None:
    """A1 → 1。"""
    letters = "".join(ch for ch in cell if ch.isalpha())
    from openpyxl.utils import column_index_from_string

    try:
        return column_index_from_string(letters)
    except Exception:  # noqa: BLE001
        return None


def _set_num_cell(ws, row: int, col: int | None, value, fmt: str | None = None) -> None:
    """写**数值**格：值 + 显示格式（默认「常规」）。

    母版是从真实报表另存的，里面常有 Excel 自动加的 `0.00000_ ` 格式 ——
    只改值不改格式的话，20 会显示成 20.00000、3.119 显示成 3.11900
    （用户 2026-10-08 反馈"会有小数点"）。母版套打路径同样是这么处理的。
    """
    from backend.reports.export_format import GENERAL, set_number_format

    _set_cell(ws, row, col, value)
    set_number_format(ws, row, col, fmt or GENERAL)


def _write_point_row(ws, row: int, f: dict, point_no: str, rec: dict,
                     control_cum: float | None = None, control_rate: float | None = None) -> None:
    from backend.reports.export_format import MM_DELTA_FORMAT

    _set_cell(ws, row, f.get("point_col"), point_no or None)
    _set_num_cell(ws, row, f.get("v0_m"), round_m(rec.get("v0_m")))
    _set_num_cell(ws, row, f.get("prev_m"), round_m(rec.get("prev_m")))
    _set_num_cell(ws, row, f.get("current_m"), round_m(rec.get("current_m")))
    # 累计/变化/速率四列要小数点（整数也显示成 2.0），与母版套打、标准版式一致
    _set_num_cell(ws, row, f.get("prev_cum_mm"), rec.get("prev_cum_mm"), MM_DELTA_FORMAT)
    _set_num_cell(ws, row, f.get("cum_mm"), rec.get("cum_mm"), MM_DELTA_FORMAT)
    _set_num_cell(ws, row, f.get("delta_mm"), rec.get("delta_mm"), MM_DELTA_FORMAT)
    _set_num_cell(ws, row, f.get("rate"), rec.get("rate"), MM_DELTA_FORMAT)
    # 控制值 I/J：与母版套打/标准版式同一个出处（预警规则的 control 级）。
    # 每条路都必须给同一种情况同一个结果：**没配规则的那一维一律留空**，
    # 既不写 0（会让所有点变成超限），也不留底模上印着的旧设计限值（那是别的基坑的数）。
    # 一类观测两个都没配时，整份文档已在 `ensure_control_rules` 被拦下。
    _set_num_cell(ws, row, f.get("control_cum"), control_cum)
    _set_num_cell(ws, row, f.get("control_rate"), control_rate)
    # 预警等级：默认不写（用户要求"导出的 Excel 不要显示预警"），见 export_options.py
    _set_cell(ws, row, f.get("warning_level"),
              (_warning_label(rec.get("warning_level")) or None) if EXPORT_SHOW_WARNING_LEVEL else None)
