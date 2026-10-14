# -*- coding: utf-8 -*-
"""Excel 日报导出 —— 按用户真实模板（八号基坑）版式生成单期日报。

模板结构（实测）：
- Sheet「竖向位移」（沉降，每测点 1 行）+ Sheet「r.立柱结构水平位移」（每测点 X/Y 2 行）
- R1 大标题（合并 A:L）；R2 工程名称（A2:F2）+ 报表编号/天气（K2:L2）
- R3 本次监测时间（A3:E3）+ 上次监测时间（K3:L3）；R4 仪器型号
- R5-R6 双层表头：A=监测点号 B=初始值(m) C=上次测值(m) D=本次测值(m) E=上次累计(mm)
  F=本次累计(mm) G=本次变化(mm) H=速率(mm/d) I=控制值(累计,合并I5:J5→R6拆I/J) K=预警等级 L=备注
- 数据从 R7 起；K 列预警等级**默认留空、不出现在导出文件里**
  （2026-09-30 用户要求；口径开关见 reports/export_options.py）
- 读值单位 m（×1000 得 mm 与变化量一致），控制值单位 mm / mm·d⁻¹
"""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.models.observation_record import ObservationRecord
from backend.repositories import observation_repo
from backend.reports.export_format import MM_DELTA_FORMAT, set_number_format
from backend.reports.export_options import EXPORT_SHOW_WARNING_LEVEL
from backend.services import calc_service
from backend.services.point_service import list_points
from backend.services.warning_service import report_control_values

# 样式常量（对齐模板）
FONT_HEAD = Font(name="宋体", size=10)
FONT_BODY = Font(name="宋体", size=11)
FONT_TITLE = Font(name="宋体", size=16, bold=True)
THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
RED_FILL = PatternFill("solid", fgColor="FFC7CE")   # 超限行红底
RED_FONT = Font(name="宋体", size=11, color="9C0006")

# 每点控制的横列数
N_COLS = 12


def _m2mm(value_m: float | None) -> float | None:
    return value_m * 1000 if value_m is not None else None


def _mm2m(value_mm: float | None) -> float | None:
    return value_mm / 1000 if value_mm is not None else None


def _write_row(ws, row: int, values: list, font: Font = FONT_BODY, fill=None) -> None:
    """写一行并加边框、居中。"""
    for col, v in enumerate(values, start=1):
        c = ws.cell(row=row, column=col, value=v)
        c.font = font
        c.alignment = CENTER
        c.border = BORDER
        if fill:
            c.fill = fill


def _set_widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


class _SeriesView:
    """某测点某分量的计算序列视图（供报表取值）。"""

    def __init__(self, rows: list[dict]):
        self.rows = rows  # calc_service 输出，含 obs_date/value_mm/cumulative/delta/rate/warning

    def latest(self, target: date | None = None) -> dict | None:
        """取目标日期及之前的最新一期有值行。"""
        for r in reversed(self.rows):
            if r["is_missing"]:
                continue
            if target and r["obs_date"] > target.isoformat():
                continue
            return r
        return None

    def previous_valid_of(self, idx_row: dict) -> dict | None:
        """该行之前的最近有值行（用于『上次测值/上次累计』）。"""
        seen = False
        for r in reversed(self.rows):
            if r is idx_row:
                seen = True
                continue
            if seen and not r["is_missing"]:
                return r
        return None


def _period_number(db, project_id: int, obs_date: date) -> str:
    """期次命名：全部观测日期（含初始值）升序，目标日期的序号 → 初始值/第N期。"""
    dates = sorted({r.obs_date for p in list_points(db, project_id, include_inactive=True) for r in observation_repo.list_by_point(db, p.id, active_only=True)})
    if not dates:
        return ""
    if obs_date not in dates:
        # 目标日期非观测日 → 找最近早于它的
        before = [d for d in dates if d <= obs_date]
        obs_date = before[-1] if before else dates[0]
    idx = dates.index(obs_date)
    return ("初始值" if idx == 0 else f"第{idx}期"), obs_date


def _warning_label(warning_level: str) -> str:
    if warning_level == "alarm":
        return "报警"
    if warning_level == "control":
        return "预警"
    return ""


def _control_values(db, project_id: int, indicator: str) -> tuple[float | None, float | None]:
    """控制值（累计 mm / 速率 mm/d）。

    取数逻辑**不在这里**：报表「控制值」的唯一出处是项目预警规则（见
    `warning_service.report_control_values`）—— 母版套打路径也走同一个函数，
    否则两条导出路径会各说各话（曾出现"母版套打照抄母版、标准版式按规则"的不一致）。
    """
    return report_control_values(db, project_id, indicator)


def _build_data_rows(db, point, series_type: str, component: str, obs_date: date) -> dict | None:
    """构建单测点单分量报表行数据；无数据返回 None。"""
    rows = calc_service.compute_point_series(db, point, series_type, component)
    if not rows:
        return None
    view = _SeriesView(rows)
    latest = view.latest(obs_date)
    if latest is None:
        return None
    prev = view.previous_valid_of(latest)

    # V0：显式初始值或首期有效读数
    if point.initial_value_mm is not None:
        v0_mm = point.initial_value_mm
    else:
        first = next((r for r in rows if not r["is_missing"]), None)
        v0_mm = first["value_mm"] if first else None

    return {
        "point_no": point.point_no,
        "series_type": series_type,
        "component": component,
        "obs_date": obs_date,
        "initial_m": _mm2m(v0_mm),
        "prev_m": _mm2m(prev["value_mm"]) if prev else None,
        "current_m": _mm2m(latest["value_mm"]),
        "prev_cum_mm": prev["cumulative_mm"] if prev else None,
        "cum_mm": latest["cumulative_mm"],
        "delta_mm": latest["delta_mm"],
        "rate": latest["rate_mm_per_day"],
        "warning_label": _warning_label(latest["warning_level"]),
        "remark": latest.get("remark"),
        "is_alarm": latest["warning_level"] == "alarm",
        "is_control": latest["warning_level"] == "control",
    }


def _write_sheet(ws, project_name: str, obs_date: date, rows: list[dict], control_cum: float | None, control_rate: float | None, is_displacement: bool):
    """写入单个数据 Sheet（竖向位移 / 水平位移），复刻模板版式。

    注意：openpyxl 合并单元格后非左上角变只读 MergedCell，
    因此必须先写单元格值、后 merge_cells。
    """
    # 列宽（对齐模板）
    _set_widths(ws, [10, 15, 14, 14, 14, 14, 13, 13, 13, 13, 11, 20])

    title = "基坑水平位移监测日报表" if is_displacement else "沉降监测报表"
    # R1 标题
    c = ws.cell(row=1, column=1, value=title)
    c.font = FONT_TITLE
    c.alignment = CENTER
    ws.row_dimensions[1].height = 30

    # R2 工程名称
    c = ws.cell(row=2, column=1, value=f"监测工程名称：{project_name}")
    c.font = FONT_HEAD
    c.alignment = Alignment(horizontal="left", vertical="center")
    c = ws.cell(row=2, column=11, value="报表编号：")
    c.font = FONT_HEAD
    c.alignment = Alignment(horizontal="left", vertical="center")

    # R3 监测时间
    c = ws.cell(row=3, column=1, value=f"本次监测时间：{obs_date.isoformat()}")
    c.font = FONT_HEAD
    c.alignment = Alignment(horizontal="left", vertical="center")
    c = ws.cell(row=3, column=11, value="上次监测时间：")
    c.font = FONT_HEAD
    c.alignment = Alignment(horizontal="left", vertical="center")

    # R4 仪器
    c = ws.cell(row=4, column=1, value="仪器型号：")
    c.font = FONT_HEAD

    # 元信息区横向合并（写在赋值之后）
    ws.merge_cells("A1:L1")
    ws.merge_cells("A2:F2")
    ws.merge_cells("K2:L2")
    ws.merge_cells("A3:E3")
    ws.merge_cells("K3:L3")
    ws.merge_cells("A4:E4")

    # R5-R6 双层表头（先写值再合并）
    headers_5 = ["监测\n点号", "初始值（m)", "上次测值（m）", "本次测值（m）", "上次累计变化量（mm)", "本次累计变化量（mm)", "本次变化量（mm)", "变化速率（mm/d）", "控制值", "", "预警\n等级", "备注"]
    headers_6 = [None, None, None, None, None, None, None, None, "累计变化量（mm)", "变化速率（mm/d）", None, None]
    for i, v in enumerate(headers_5, start=1):
        if i in (9, 10):
            continue  # I5:J5 合并，稍后处理
        c = ws.cell(row=5, column=i, value=v)
        c.font = FONT_HEAD
        c.alignment = CENTER
        c.border = BORDER
    # I5:J5 合并后写入控制值
    ws.merge_cells("I5:J5")
    c = ws.cell(row=5, column=9, value="控制值")
    c.font = FONT_HEAD
    c.alignment = CENTER
    c.border = BORDER
    ws.cell(row=5, column=10).border = BORDER
    for i, v in enumerate(headers_6, start=1):
        if v is None:
            continue
        c = ws.cell(row=6, column=i, value=v)
        c.font = FONT_HEAD
        c.alignment = CENTER
        c.border = BORDER
    # 竖向合并：A5:A6 等（列 1-8、11-12 表头竖跨两行）
    for col_letter in ("A", "B", "C", "D", "E", "F", "G", "H", "K", "L"):
        ws.merge_cells(f"{col_letter}5:{col_letter}6")
    ws.row_dimensions[5].height = 30
    ws.row_dimensions[6].height = 22

    # 数据区（R7 起）
    r = 7
    for row in rows:
        # 预警等级列 + 报警行的红底红字：默认都不出现（用户要求"导出的 Excel 不要显示预警"），
        # 口径开关见 reports/export_options.py。
        # ⚠ 不是"统一写正常" —— 真有报警却报正常属于伪造监测数据，任何情况下都不做。
        alert = EXPORT_SHOW_WARNING_LEVEL and row.get("is_alarm")
        font = RED_FONT if alert else FONT_BODY
        fill = RED_FILL if alert else None
        values = [
            row["point_no"],
            row["initial_m"],
            row["prev_m"],
            row["current_m"],
            row["prev_cum_mm"],
            row["cum_mm"],
            row["delta_mm"],
            row["rate"],
            control_cum,
            control_rate,
            row["warning_label"] if EXPORT_SHOW_WARNING_LEVEL else None,
            row["remark"],
        ]
        _write_row(ws, r, values, font=font, fill=fill)
        # 上次累计(E)/本次累计(F)/本次变化(G)/速率(H)：毫米读数要能看出小数点
        #（整数也显示成 2.0，用户 2026-10-08 要求）。母版套打、旧模板路径同口径。
        for col in (5, 6, 7, 8):
            set_number_format(ws, r, col, MM_DELTA_FORMAT)
        ws.row_dimensions[r].height = 20
        r += 1

    # 说明行（正负号说明 / 监测结论留空待填）
    sign_txt = "变化量正负号说明：“+”为下沉/向基坑内，“-”为上升/向基坑外（以设计文件为准）" if not is_displacement else \
        "变化量正负号说明：X 正数朝左线负数朝右线；Y 正数朝大里程负数朝小里程（以设计文件为准）"
    c = ws.cell(row=r + 1, column=1, value=sign_txt)
    c.font = FONT_HEAD
    ws.merge_cells(start_row=r + 1, start_column=1, end_row=r + 1, end_column=12)
    c = ws.cell(row=r + 2, column=1, value="监测结论及建议：")
    c.font = FONT_HEAD
    ws.merge_cells(start_row=r + 2, start_column=1, end_row=r + 2, end_column=12)
    c = ws.cell(row=r + 3, column=1, value=f"本次监测时间：{obs_date.isoformat()}")
    c.font = FONT_HEAD
    ws.merge_cells(start_row=r + 3, start_column=1, end_row=r + 3, end_column=12)


def build_period_excel(db, project_id: int, obs_date: date | None = None) -> tuple[BytesIO, str, dict]:
    """生成单期日报 Excel（内存字节流）。

    返回 (bytes_io, filename, info)。
    注意：本函数只读取原始记录 + 引擎结果，不含任何业务公式。
    """
    from backend.services.project_service import get_project

    project = get_project(db, project_id)
    points = [p for p in list_points(db, project_id, include_inactive=False)]

    # 确定目标日期（缺省取最近观测日）
    all_dates = sorted({
        r.obs_date
        for p in points
        for r in observation_repo.list_by_point(db, p.id, active_only=True)
    })
    if not all_dates:
        raise ValueError("该项目暂无观测数据，无法生成日报")
    target = obs_date or all_dates[-1]

    # 期次
    period_label, eff_date = _period_number(db, project_id, target)

    wb = Workbook()
    # ---- Sheet 1: 竖向位移 ----
    ws1 = wb.active
    ws1.title = "竖向位移"
    s_rows = []
    for p in points:
        if p.point_type not in ("settlement", "both"):
            continue
        row = _build_data_rows(db, p, "settlement", "value", eff_date)
        if row:
            s_rows.append(row)
    control_cum_s, control_rate_s = _control_values(db, project_id, "settlement")
    _write_sheet(ws1, project.name, eff_date, s_rows, control_cum_s, control_rate_s, is_displacement=False)

    # ---- Sheet 2: r.立柱结构水平位移 ----
    ws2 = wb.create_sheet("r.立柱结构水平位移")
    d_rows = []
    for p in points:
        if p.point_type not in ("displacement", "both"):
            continue
        for comp in ("X", "Y"):
            row = _build_data_rows(db, p, "displacement", comp, eff_date)
            if row:
                d_rows.append(row)
    control_cum_d, control_rate_d = _control_values(db, project_id, "displacement")
    _write_sheet(ws2, project.name, eff_date, d_rows, control_cum_d, control_rate_d, is_displacement=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"{project.code}_基坑监测日报_{period_label}_{eff_date.isoformat().replace('-', '.')}.xlsx"
    alarm_count = sum(1 for r in s_rows + d_rows if r["is_alarm"])
    control_count = sum(1 for r in s_rows + d_rows if r["is_control"])
    info = {
        "project_code": project.code,
        "project_name": project.name,
        "obs_date": eff_date.isoformat(),
        "period_label": period_label,
        "settlement_rows": len(s_rows),
        "displacement_rows": len(d_rows),
        "alarm_count": alarm_count,
        "control_count": control_count,
        "filename": filename,
    }
    return buf, filename, info
