# -*- coding: utf-8 -*-
"""母版整卷日报引擎（V2）—— 学习样板文件 → 每期产出完整报送整卷。

设计（用户问答锁定规格）：
- 用户上传一份"样板文件"（如已填好的第 21 期整卷，9 个 Sheet）；
- 学习阶段自动识别【每期变量槽】：封面/签字 的 日期格、期次格；
  数据 Sheet（竖向位移/r.立柱）的 表头行、点号列、数据区列映射、数据起始行；
- 其余 Sheet 与单元格（概况/分析/预警统计/成果提交/T.立柱 等固定文案）原样保留；
- 每期导出 = 复制母版 → 填变量槽（日期/期次/天气/数据区数值）→ 另存。

关键实现点：
1. 数据表 E/F/G/H 列在母版是公式 —— 用户要求导出时【全填数值】，
   系统按 Calculation Engine 结果写值（覆盖公式），不依赖打开 Excel 重算。
2. 竖向位移每测点 1 行；r.立柱 X 行有点号 + Y 行无点号相邻（B 列初始值）。
3. 期次按观测序号自动算：初始值=第0期 → 第N期。
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
from backend.reports.export_format import round_m, round_mm, round_rate
from backend.reports.export_options import EXPORT_SHOW_WARNING_LEVEL
from backend.reports.master_learn import is_period_slot_text
from backend.services.project_service import get_project


def build_master_report(
    db: Session,
    project_id: int,
    tpl: ExportTemplate,
    obs_date: date | None = None,
    weather: str = "",
) -> tuple[BytesIO, str, dict]:
    """以母版为底模生成最新一期（或指定期）完整整卷日报。

    返回 (bytes_io, filename, info)。
    """
    from backend.services.point_service import list_points
    from backend.repositories import observation_repo
    from backend.services.warning_service import ensure_control_rules

    project = get_project(db, project_id)
    if not tpl.file_path or not Path(tpl.file_path).exists():
        raise ValueError("母版原件缺失，请重新上传")

    layout = tpl.layout_json or {}
    # 母版里唯一由系统决定的格子是 I/J「控制值」—— 项目没配规则就**不出这份文档**，
    # 而不是照抄母版上印着的旧限值（用户 2026-10-14：与生成数据统一为"拦住"）。
    ensure_control_rules(db, project_id, {s.get("kind") for s in (layout.get("sheets") or []) if s.get("kind")})

    # 目标期次与日期：默认最新有完整数据的一期
    points = list_points(db, project_id, include_inactive=False)
    all_dates = sorted({r.obs_date for p in points for r in observation_repo.list_by_point(db, p.id, active_only=True)})
    if not all_dates:
        raise ValueError("该项目暂无观测数据")

    from collections import Counter

    counts = Counter()
    for p in points:
        for r in observation_repo.list_by_point(db, p.id, active_only=True):
            if r.value_mm is not None:
                counts[r.obs_date] += 1
    target = obs_date or (max((d for d, c in counts.items() if c >= max(counts.values()) * 0.8)) if counts else all_dates[-1])

    # 期次：全部观测日（含初始值）升序序号
    period_no = all_dates.index(target)  # 0 = 初始值日
    is_initial = period_no == 0

    wb = load_workbook(tpl.file_path, data_only=False)  # 保留原样式与公式结构（数据区将由引擎写值）

    # ---- 0) 工程名称统一：以系统项目名为准改写母版各页工程名称槽 ----
    _sync_project_name(wb, project.name)

    # ---- 1) 期次/日期变量槽（layout 记录 + 字面兜底）----
    meta = layout.get("meta") or {}
    # 期次文本形态跟随样本格（母版带括号 → 输出带括号），多槽全写，保证签字/封面同步变化
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

    # 数据表时间文本：本次/上次监测时间（若 layout 记录位置）
    for scfg in layout.get("sheets") or []:
        f = scfg.get("layout") or {}
        sn = scfg.get("sheet_name", "")
        if sn not in wb.sheetnames:
            continue
        # 找该 Sheet 行 3 的"本次监测时间：…"与"上次监测时间：…"并整体替换日期
        ws_t = wb[sn]
        prev_date = _prev_obs_date(db, project_id, target)
        # 初始值期（无上一观测日）：K3 也填初始值日（与用户真实初始值模板一致）
        k3_date = prev_date if prev_date else (target if is_initial else None)
        for r in (1, 2, 3, 4):
            for c in range(1, 20):
                cell = ws_t.cell(row=r, column=c)
                v = str(cell.value or "")
                if "本次监测时间" in v:
                    cell.value = _replace_date_label(v, target)
                elif "上次监测时间" in v:
                    if k3_date:
                        cell.value = _replace_date_label(v, k3_date)
                    else:
                        # 非初始值期且确无上一期（异常态）→ 保留前缀、清空日期避免串入母版旧值
                        cell.value = v.split("：")[0].split(":")[0].strip() + "："
    # ---- 2) 数据 Sheet 数据区填充（全数值）----
    # 自动补点摘要要带出去：母版只有 8 个点位、项目有 12 个测点时，导出文件会比母版多几行，
    # 界面得能说清"为什么多了几行"，否则用户会以为导错了。
    fill_summary = fill_data_sheets(db, project_id, wb, layout, target, weather=weather, is_initial=is_initial)

    # ---- 3) 原始数据区（每期同步：日期 + X/Y/Z 点号坐标）----
    raw_summary = fill_raw_data_blocks(db, project_id, wb, layout, target, is_initial=is_initial)

    # ---- 4) 母版自带的「××统计表」：清掉旧期数据（那是别的基坑的点位与预警等级）----
    cleared_stats = clear_stale_stat_tables(wb)

    # ---- 5) 概况/分析/成果提交三页的**整段文字**：按本期数据重写 ----
    # 这三页装的是文字而不是变量槽；母版里带着**别的工程**的内容（实测五份母版这三页逐字相同，
    # 分析页写着"地表沉降 DBC05-01""测斜QCX1"，结论页写着"屿前站"）。不清就是在发别人的东西，
    # 而让用户每期手改 12 段也不现实 → 按本期真实数据生成。
    text_blocks = _ensure_text_blocks(db, tpl, layout)
    text_summary: dict = {}
    if text_blocks:
        from backend.reports.template_excel import _series_rows
        from backend.reports.text_blocks import GenContext, apply_text_blocks

        rows = _series_rows(db, project_id, target)
        kinds = _kinds_for_report(layout, rows)
        text_summary = apply_text_blocks(
            wb,
            text_blocks,
            GenContext(
                project_name=project.name,
                obs_date=target,
                period_text=("初始值" if is_initial else f"第{_cn_period(period_no)}期"),
                is_initial=is_initial,
                rows=rows,
                kinds=kinds,
            ),
        )

    buf = BytesIO()
    wb.save(buf)
    wb.close()
    buf.seek(0)

    # 文件名用与页内一致的期次文本（随样本带括号形态）
    filename = f"{project.name}{period_text}{target.isoformat().replace('-', '.')}.xlsx"
    info = {
        "project_code": project.code,
        "project_name": project.name,
        "obs_date": target.isoformat(),
        "period": f"第{_cn_period(period_no)}期" if period_no > 0 else "初始值",
        "filename": filename,
        "weather": weather,
        "template_points": fill_summary.get("template_points") or {},
        "added_points": fill_summary.get("added") or {},
        "shifted_rows": fill_summary.get("shifted") or {},
        "missing_raw_points": (raw_summary or {}).get("missing") or {},
        "added_raw_points": (raw_summary or {}).get("added") or {},
        "cleared_stats": cleared_stats,
        "text_generated": text_summary.get("generated") or {},
        "text_cleared": text_summary.get("cleared") or {},
        "text_overflow": text_summary.get("overflow") or {},
    }
    return buf, filename, info


def _kinds_for_report(layout: dict, rows: list[dict]) -> list[str]:
    """本期报表要生成几类监测：以母版的表结构为准（顺序稳定），数据里有而母版没有的补在后面。"""
    kinds: list[str] = []
    for scfg in layout.get("sheets") or []:
        k = scfg.get("kind")
        if k and k not in kinds:
            kinds.append(k)
    for r in rows:
        k = r.get("series_type")
        if k and k not in kinds:
            kinds.append(k)
    return kinds


def _ensure_text_blocks(db: Session, tpl, layout: dict) -> list[dict]:
    """取母版的文字区块；**老母版（升级前上传的）没有这个字段 → 就地补学一次并写回**。

    为什么要自动补学：不想让用户为了这个功能重新上传一遍母版 —— 原件就在盘上，
    重新学一遍文字块即可（只补 `text_blocks` 这一个键，**不动已识别好的 meta/sheets**，
    避免把原本能用的数据表布局改坏）。
    学习结果即使是空列表也会写回：空列表 = "已经学过、这份母版没有可生成的文字页"，
    这样下次导出不用再学一遍。
    """
    if "text_blocks" in layout:
        return layout.get("text_blocks") or []

    from backend.reports.master_learn import learn_master_layout

    blocks: list[dict] = []
    try:
        if tpl.file_path and Path(tpl.file_path).exists():
            learned = learn_master_layout(Path(tpl.file_path))
            blocks = learned.get("text_blocks") or []
    except Exception:  # noqa: BLE001 —— 学不出来就维持原样（宁可不改，也不能让导出挂掉）
        blocks = []

    try:
        merged = dict(layout)
        merged["text_blocks"] = blocks
        tpl.layout_json = merged
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return blocks


_NAME_ENTITY = re.compile(
    r"[0-9０-９一二三四五六七八九十]{1,3}号[^，。；;：:、\s（）()【】\[\]{}]{0,20}(?:基坑|墩)"
)

# 名称标签：标签后面写的就是这份母版"自称的基坑名"
NAME_LABELS = ("监测工程名称：", "工程名称：", "工点名称：", "基坑名称：", "项目名称：")


def _looks_like_project_name(s: str) -> bool:
    """像不像一个基坑名（用来从母版里认"旧名字"）。

    必须是"某个编号的基坑"：**含数字**（阿拉伯或中文数字）**且**含"号/基坑/墩"。
    光一个「基坑」「兴善公路」「2026年8月20日」都不是名字 —— 认错了会拿它到处替换。
    """
    if not (2 <= len(s) <= 14):
        return False
    if any(ch in s for ch in "，。；;：:、（）()【】[]{}%～~ \n\t"):
        return False
    if not any(ch in _DIGIT_CHARS for ch in s):
        return False
    return ("号" in s) or ("基坑" in s) or ("墩" in s)


# 数字字符（阿拉伯 + 中文数字），用于判断"这是个编号"
_DIGIT_CHARS = set("0123456789０１２３４５６７８９一二三四五六七八九十百千零")


def _extract_master_project_name(wb) -> str | None:
    """读出**母版自称的基坑名**（如「十一号」）—— 用来把它整体换成当前项目名。

    为什么需要：母版常是拿**别的基坑**那期报表另存的（用户 2026-10-08 报障：
    项目叫「十号基坑」，导出来却是「十一号」）。系统只替换它学到的变量槽
    （日期/期次/数据），母版里那些**固定文字**（封面工程名称、签字、结论段…）
    会原样留着，报表上就全是别人的基坑名。

    取法：只看名称标签格（工点名称/监测工程名称/工程名称…），标签后按空白切分、
    **最后一段**就是名字（前面是"兴善公路"这类地名前缀，保留不动）。
    取出现次数最多的；**出现不足 2 次就不认** —— 宁可不换，也别拿一个来路不明的词到处替换。
    """
    from collections import Counter

    from openpyxl.cell.cell import MergedCell

    cand: Counter = Counter()
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell) or cell.value is None:
                    continue
                t = str(cell.value)
                for lb in NAME_LABELS:
                    i = t.find(lb)
                    if i < 0:
                        continue
                    parts = [p for p in re.split(r"[\s\u3000]+", t[i + len(lb):].strip()) if p]
                    if not parts:
                        break
                    name = parts[-1].strip("（）()【】[]")
                    if _looks_like_project_name(name):
                        cand[name] += 1
                    # 一格只认一次：「监测工程名称：」本身也含「工程名称：」，
                    # 不 break 会把同一处数成两次，把"单处命中"错判成"出现两次"。
                    break
    for name, n in cand.most_common():
        if n >= 2:
            return name
    return None


def _sync_project_name(wb, project_name: str) -> int:
    """把母版中的工程名称统一改写为系统项目名（以系统为准，随项目改名自动跟随）。

    覆盖范围：
    1) **母版自称的旧基坑名**（`_extract_master_project_name` 认出来的那个，如「十一号」）
       —— 全簿整体替换，封面工程名称格、签字"（十一号）"、数据表"监测工程名称：…/
       工点名称：兴善公路 …"、以及叙述文本（"本次监测工作为十一号 围檩…"）一次覆盖。
       旧名后面若紧跟"基坑/墩"（"十一号基坑"）连后缀一起吃，避免换成"十号基坑基坑"。
    2) **数字号型基坑实体**（如"5号右边墩基坑"）—— 母版封面号常照抄错误，
       此类笔误一并修正。概况里的 "Y11#""Z21~Z24#墩" 等 # 型编号、大工程名
       （…上跨沪昆铁路立交工程）不含"数字号…基坑/墩"实体结构，天然不受影响。

    ⚠ 旧名与项目名"互相包含"时**不动**（例如旧名"十号"、项目名"十号基坑"）——
       否则会替成"十号基坑基坑"。
    """
    from openpyxl.cell.cell import MergedCell

    old = _extract_master_project_name(wb)
    replacer = None
    if old and old != project_name and old not in project_name:
        replacer = re.compile(re.escape(old) + r"(?:基坑|墩)?")

    changed = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell) or cell.value is None:
                    continue
                t = str(cell.value)
                if not t.strip():
                    continue
                nt = t
                if replacer is not None:
                    nt = replacer.sub(lambda m: project_name, nt)
                if _NAME_ENTITY.search(nt):
                    nt = _NAME_ENTITY.sub(project_name, nt)
                if nt != t:
                    cell.value = nt
                    changed += 1
    return changed


def _cn_period(no: int) -> str:
    """数字转中文期次：1→一 … 21→二十一；100 期以上用阿拉伯数字。"""
    digits = "零一二三四五六七八九"
    if no < 10:
        return digits[no]
    if no < 100:
        # 10~19 的"十"前面不写"一"（十、十一 …），20 以上才带个位数字
        tens = "" if no < 20 else digits[no // 10]
        ones = "" if no % 10 == 0 else digits[no % 10]
        return f"{tens}十{ones}"
    return str(no)


def _collect_period_cells(wb, layout: dict) -> list[str]:
    """汇总本期需写入的期次槽。

    来源（去重后按序）：
    1. layout.meta.period_cells（新版学习结果，可含签字等多处期次）；
    2. layout.meta.period_cell（旧版学习结果，兼容存量模板）；
    3. 字面兜底：非数据 Sheet 顶部整格形如"第X期/（第X期）"（含"第"字期次）或
       签字/封面页整格"初始值/（初始值）"的格。治愈"母版是初始值期样板（文本为
       （初始值），不含'第'字）导致 layout 未学到 period_cell"的存量模板 ——
       即便 layout 缺失期次槽，导出仍会随日期更新整卷期次标识。
       概况/分析页里"与初始值对比"等固定表头（整格'初始值'）不会被误改。
    """
    from openpyxl.utils import get_column_letter

    meta = layout.get("meta") or {}
    cells: list[str] = []
    for ref in meta.get("period_cells") or []:
        if ref and ref not in cells:
            cells.append(ref)
    pc = meta.get("period_cell")
    if pc and pc not in cells:
        cells.append(pc)

    data_sheets = {s.get("sheet_name") for s in layout.get("sheets") or []}
    for sn in wb.sheetnames:
        if sn in data_sheets:
            continue
        ws = wb[sn]
        for r in range(1, min(ws.max_row or 1, 21) + 1):
            for c in range(1, min(ws.max_column or 1, 13) + 1):
                v = ws.cell(row=r, column=c).value
                if v is None:
                    continue
                t = str(v).strip()
                if not t or not is_period_slot_text(t, sn):
                    continue
                ref = f"{sn}!{get_column_letter(c)}{r}"
                if ref not in cells:
                    cells.append(ref)
    return cells


def _period_text(no: int, sample=None) -> str:
    """期次文本。形态跟随母版样本格：样本带括号 → 输出带全角括号，否则不带。

    sample 缺省（无任何期次槽）时保持旧行为（全角括号），文件名等处仍可用。
    """
    core = "初始值" if no == 0 else f"第{_cn_period(no)}期"
    if sample is None:
        return f"（{core}）"
    s = str(sample).strip().replace(" ", "").replace("\u3000", "")
    if s.startswith("（") or s.startswith("("):
        return f"（{core}）"
    return core


def _replace_date_label(text: str, d: date) -> str:
    """'本次监测时间：2026-08-20' 或 '本次监测时间：2026.8.20' → 替换日期保留前缀。"""
    prefix = text.split("：")[0].split(":")[0].strip() + "："
    return f"{prefix}{d.isoformat()}"


def _prev_obs_date(db, project_id: int, target: date):
    """该项目 target 之前的最近观测日。"""
    from backend.repositories import observation_repo
    from backend.services.point_service import list_points

    dates = sorted({
        r.obs_date
        for p in list_points(db, project_id, include_inactive=False)
        for r in observation_repo.list_by_point(db, p.id, active_only=True)
    })
    before = [d for d in dates if d < target]
    return before[-1] if before else None


def _write_text_cell(wb, cell_ref: str | None, value: str) -> None:
    if not cell_ref:
        return
    ws, row, col = _resolve_cell(wb, cell_ref)
    if ws is not None:
        ws.cell(row=row, column=col, value=value)


def _write_value_cell(wb, cell_ref: str | None, value) -> None:
    if not cell_ref:
        return
    ws, row, col = _resolve_cell(wb, cell_ref)
    if ws is not None:
        ws.cell(row=row, column=col, value=value)


def _write_date_cell(wb, cell_ref: str | None, d: date) -> None:
    """往封面/签字的日期槽写日期，并保证那一格**看起来是日期**。

    写的是 Excel 序列号（母版上那一格本来就是序列号），能不能显示成 2026-10-16
    全看单元格的数字格式。十号基坑的母版上 `封面!F16` 是 General 格式 ——
    只写序列号，成果文件的封面就印着 `46311`（2026-10-16 在界面预览里当场看见）。
    槽位自带日期格式时一律不改（那是用户自己的排版）。
    """
    from backend.reports.export_format import DATE_FORMAT, needs_date_format, set_number_format

    if not cell_ref:
        return
    ws, row, col = _resolve_cell(wb, cell_ref)
    if ws is None:
        return
    ws.cell(row=row, column=col, value=_excel_serial(d))
    if needs_date_format(ws.cell(row=row, column=col).number_format):
        set_number_format(ws, row, col, DATE_FORMAT)


def _resolve_cell(wb, cell_ref: str):
    """'封面!F16' 或 'F16' → (ws, row, col)。"""
    sheet_part, _, coord = cell_ref.partition("!")
    if not coord:
        coord = cell_ref
        sheet_part = wb.sheetnames[0]
    letters = "".join(ch for ch in coord if ch.isalpha())
    digits = "".join(ch for ch in coord if ch.isdigit())
    from openpyxl.utils import column_index_from_string

    try:
        col = column_index_from_string(letters)
        return wb[sheet_part], int(digits), col
    except Exception:
        return None, 0, 0


def _excel_serial(d: date) -> int:
    """日期 → Excel 序列号（1900 系统，含 1900-02-29 bug 兼容）。"""
    # 与 Excel 一致：1900-01-01 = 1；1900-02-29 视为存在（序列号 60）
    base = date(1899, 12, 30)
    return (d - base).days


_RAW_ROWS = 3      # 原始数据区里每个测点占 3 行（X / Y / Z）


def _raw_side_cols(raw: dict, side: str) -> tuple[int, int, int]:
    """原始数据区某一列的 (点号列, 分量列, 坐标值列) 列号。

    母版没排这一列（比如右列一个点都没有）时，按母版惯例「右列 = 左列 +3 列」推断
    （竖向位移表：左 N/O/P、右 Q/R/S）。
    """
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

    def _col(coord: str) -> int:
        return column_index_from_string(coordinate_from_string(coord.split("!")[-1])[0])

    entries = raw.get(side) or []
    if entries:
        e = entries[0]
        return (_col(e["point_cell"]), _col(e["component_cell"]), _col(e["value_cell"]))
    other = "left" if side == "right" else "right"
    if not (raw.get(other) or []):
        raise KeyError(f"布局的原始数据区里 {side}/ {other} 列都没有点位块，无法推断列位置")
    pc, cc, vc = _raw_side_cols(raw, other)
    shift = 3 if side == "right" else -3
    return (pc + shift, cc + shift, vc + shift)


def _write_raw_block(ws, start_row: int, pcol: int, ccol: int, vcol: int,
                     point_no: str, data: dict) -> None:
    """写一个原始数据点位块：点号（首行）+ X/Y/Z 分量 + 三个坐标值。

    ⚠ 母版里**分量列（X/Y/Z 字样）是母版自己带的文字**，原实现只写坐标值列，
    新补的块如果不写分量列，就会变成"只有数字、不知道哪行是 X"。
    写之前先把这三格清掉，防止母版在那些位置残留别的点位的旧数据。
    """
    for i in range(_RAW_ROWS):
        _set(ws, start_row + i, ccol, None)
        _set_num(ws, start_row + i, vcol, None)   # 坐标值列顺手把格式归位
    _set(ws, start_row, pcol, point_no)
    for i, comp in enumerate(("X", "Y", "Z")):
        _set(ws, start_row + i, ccol, comp)
        v = data.get(comp)
        _set_num(ws, start_row + i, vcol, round_m(v))


def fill_raw_data_blocks(db: Session, project_id: int, wb, layout: dict, target: date, is_initial: bool = False) -> dict:
    """填母版"原始数据"区（X/Y/Z 点号坐标表）——固定显示各测点 V0 初始值，不随期次变。

    用户语义：该区是"基坑测点的原始数据基准"（X=东/E、Y=北/N、Z=沉降高程），
    展示的是初始状态那组坐标；本期观测数据在上方数据表体现，本区不每期更新。
    - 坐标：取各测点/分量的 V0（initial_value_mm 或首个有效读数），单位米
    - 日期：固定显示项目的初始值基准日（最早观测日），非本期 target
    - 无 V0 的格子清空（防母版旧值泄漏）

    返回 {"added": {sheet: [补进去的点号]}, "missing": {sheet: [网格放不下的点号]}}。
    这一区是母版定死的「两列 × 每点 3 行」网格，母版往往只填了 8 个块（左 6 右 2），
    但**右列下面通常已经预留了带边框的空格** —— 12 个测点正好塞得下（2026-09-30 用户报障：
    "这个没有跟上?"，截图里 JJC9~JJC12 在原始数据区整条缺失）。
    网格真的排满了还放不下的，报进 "missing"，**不静默丢**。
    """
    summary: dict = {}
    from backend.reports.master_expand import plan_raw_slots
    from backend.reports.template_excel import _series_rows
    from backend.repositories import observation_repo
    from backend.services.point_service import list_points
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

    rows = _series_rows(db, project_id, target)  # v0_m 字段即初始值（米）
    # {point_no: {X: m, Y: m, Z: m}} —— 取 V0（非本期 current）
    by_point: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("v0_m") is None:
            continue
        comp = r["component"]
        d = by_point.setdefault(r["point_no"], {})
        if comp == "X":
            d["X"] = r["v0_m"]
        elif comp == "Y":
            d["Y"] = r["v0_m"]
        elif comp == "value":
            d["Z"] = r["v0_m"]

    # 初始值基准日 = 项目最早有效观测日（V0 采测日）
    v0_date = None
    all_ds: list[date] = []
    for p in list_points(db, project_id, include_inactive=False):
        for rec in observation_repo.list_by_point(db, p.id, active_only=True):
            if rec.value_mm is not None:
                all_ds.append(rec.obs_date)
    if all_ds:
        v0_date = min(all_ds)

    for scfg in layout.get("sheets") or []:
        raw = scfg.get("raw_data")
        if not raw:
            continue
        sn = scfg.get("sheet_name", "")
        if sn not in wb.sheetnames:
            continue
        ws = wb[sn]

        # 日期值格 → 固定写初始值基准日（非本期）
        date_coord = raw.get("date_value_cell")
        if date_coord and "!" not in date_coord and v0_date is not None:
            col_letter, _row = coordinate_from_string(date_coord)
            _set(ws, _row, column_index_from_string(col_letter), v0_date.isoformat())

        # 左/右两列点号：每个点 3 行（X/Y/Z）按起点行偏移
        slots = [(side, e) for side in ("left", "right") for e in (raw.get(side) or [])]
        cols = {side: _raw_side_cols(raw, side) for side in ("left", "right")}

        # 槽位 ↔ 本项目测点 的对应关系（两遍，尊重母版原有排布）：
        #   ① 槽位自己写的点号若本项目也有 → 原地保留；
        #   ② 剩下的测点按顺序填进还空着的槽位。
        # 为什么必须重写点号标签：模板现在可以**跨项目共用**（一份母版绑好几个基坑），
        # 12 点的模板拿去导 10 点的项目，不重写就会在报表上留下 JJC11/JJC12 两个
        # **空坐标的点号**（用户报障的同源问题）。多余槽位连标签一起清掉。
        ordered = sorted(by_point, key=_no_key)
        slot_of: dict[str, int] = {}
        for i, (_side, entry) in enumerate(slots):
            pno = entry.get("point_no")
            if pno in by_point and pno not in slot_of:
                slot_of[pno] = i
        free = [i for i in range(len(slots)) if i not in set(slot_of.values())]
        rest = [p for p in ordered if p not in slot_of]
        for i, pno in zip(free, rest):
            slot_of[pno] = i
        assigned = {i: p for p, i in slot_of.items()}

        for i, (side, entry) in enumerate(slots):
            pno = assigned.get(i)
            start_row = entry["start_row"]
            pcol, _ccol, vcol = cols[side]           # 点号列 / 分量列 / 坐标值列
            _set(ws, start_row, pcol, pno)           # 空槽位 → None，把母版的旧点号清掉
            data = by_point.get(pno) or {}
            _set_num(ws, start_row + 0, vcol, round_m(data.get("X")))
            _set_num(ws, start_row + 1, vcol, round_m(data.get("Y")))
            _set_num(ws, start_row + 2, vcol, round_m(data.get("Z")))

        # 母版网格没覆盖到的测点：接着空位往下补（母版右列**已经预留了带边框的空格**）。
        # 网格到底了放不下的，报进 summary["missing"]，**绝不静默丢点**。
        want = [p for p in ordered if p not in slot_of]
        placed: list[str] = []
        if want:
            bottom = max((e["start_row"] + _RAW_ROWS - 1 for _, e in slots), default=0)
            for pno, (side, start_row) in zip(want, plan_raw_slots(raw, bottom, len(want), _RAW_ROWS)):
                pcol, ccol, vcol = cols[side]
                data = by_point.get(pno) or {}
                _write_raw_block(ws, start_row, pcol, ccol, vcol, pno, data)
                slot_of[pno] = len(slots) + len(placed)
                placed.append(pno)
            if placed:
                summary.setdefault("added", {})[sn] = placed

        missing = [p for p in ordered if p not in slot_of]
        if missing:
            summary.setdefault("missing", {})[sn] = missing
    return summary


def fill_data_sheets(db: Session, project_id: int, wb, layout: dict, target: date, weather: str = "", is_initial: bool = False) -> dict:
    """把数据写进数据 Sheet。返回补点摘要（给导出提示用）。

    layout["sheets"]: [{sheet_name, kind: settlement/displacement, layout:{point_col, col map, data_start_row}}]
    用户要求数值全填（覆盖模板公式）。
    is_initial=True（初始值期）：数据区仅保留 B 初始值（与点号），C~H 累计/变化/速率与 K 预警等级全部清空——
    母版是已填好的某一期，其自带的上期数据/公式/旧时间不得串入初始值报表。

    【自动补点（2026-09-30 用户需求）】母版往往是从**别的基坑**拿来的，点位行数（8）比项目测点数（12）少。
    原实现按母版固有行位逐点驱动，多出来的点**整条消失且无提示**。现在：
    · 母版点位行照旧按原行位写（保留空行语义不变）；
    · 项目里多出来的点**追加**在最后一个点位行之后，行格式沿用它；
    · 下面放不下时，把「说明/结论/签字」整块下移（见 master_expand.shift_rows_down）。
    """
    from backend.reports.template_excel import _series_rows  # 复用取数（含 prev/current/cum/delta/rate）
    from backend.services.warning_service import report_control_values  # 控制值唯一出处

    data = _series_rows(db, project_id, target)
    summary: dict = {"template_points": {}, "added": {}, "shifted": {}}

    for scfg in layout.get("sheets") or []:
        sn = scfg.get("sheet_name", "")
        if sn not in wb.sheetnames:
            continue
        ws = wb[sn]
        f = scfg.get("layout") or {}
        start = f.get("data_start_row") or 7
        kind = scfg.get("kind") or ("displacement" if "水平" in sn else "settlement")
        subset = [r for r in data if r["series_type"] == kind]

        # 天气格（如 K2 单元格原本"天气：晴"整格文本 → 保留前缀替换天气）
        if weather and f.get("weather_cell"):
            ws2, rr, cc = _resolve_cell(wb, f["weather_cell"])
            if ws2 is not None:
                old = str(ws2.cell(row=rr, column=cc).value or "")
                if "：" in old or ":" in old:
                    prefix = old.split("：")[0].split(":")[0].strip() + "："
                    ws2.cell(row=rr, column=cc).value = f"{prefix}{weather}"
                else:
                    ws2.cell(row=rr, column=cc).value = weather

        # 点号→该日记录 映射：竖向位移每点一行(A列点号)；位移 X 行 A 有点号、Y 行相邻（A 空）
        by_point: dict[str, list[dict]] = {}
        for r in subset:
            by_point.setdefault(r["point_no"], []).append(r)

        # ── 点位行块 = 母版固有的 + 项目多出来的（自动补行） ──
        blocks = _master_point_blocks(ws, start, f, kind == "displacement")
        summary["template_points"][sn] = len(blocks)
        blocks, added, shifted = _expand_blocks(ws, f, kind, blocks, db, project_id)
        if added:
            summary["added"][sn] = added
        if shifted:
            summary["shifted"][sn] = shifted

        # 按【母版固有行位】逐点驱动（而非按有数据的点连续下推）：
        #   - 有点位行一律处理：当日有记录 → 写数值覆盖；无记录 → 清空该行数据列，
        #     绝不让母版自带的旧值/旧公式（含旧速率公式 G/2 等）串入导出文件。
        #   - 修复历史 bug：某点某日缺测时按有数据的点下推会造成整表点位错位，
        #     末尾残留母版旧行；某 Sheet 整体无记录时整表残留母版旧数据。
        # ── 「控制值」两列按**项目预警规则**写（与系统口径一一对应）──
        # 母版是拿别的基坑的表改的，它自带的 30/10、20/3 只是那一期的旧值；
        # 系统里改了限值，报表必须跟着变，否则"表里写 30/10、系统按 20/2 判"。
        # 一类观测两个值都没配 → 整份文档在 `ensure_control_rules` 就被拦下；
        # 只配了一项 → 缺的那格留空（**绝不写 0，也绝不沿用母版原值**）。
        ctrl_cum, ctrl_rate = report_control_values(db, project_id, kind)

        # 本项目**真实存在**的测点（该 Sheet 对应类型）—— 模板现在可跨项目共用，
        # 母版的点位行可能比本项目多（12 行给 10 个点的项目），那些"别的基坑的点号"
        # 必须连标签一起清掉，否则报表上会冒出空坐标/空数据的 JJC11、JJC12。
        from backend.services.point_service import list_points

        alive = {
            p.point_no
            for p in list_points(db, project_id, include_inactive=False)
            if p.point_type == "both" or p.point_type == kind
        }

        for point_no, rows in blocks:
            comps = by_point.get(point_no) or []
            for i, rrow in enumerate(rows):
                if kind == "settlement":
                    rec = next((c for c in comps if c["component"] == "value"), None)
                else:
                    rec = next((c for c in comps if c["component"] == ("X" if i == 0 else "Y")), None)
                is_main = kind == "settlement" or i == 0
                # 控制值是**整张表共同的设计限值**，与该点当天有没有读数无关 → 每行都写
                _write_control_values(ws, rrow, f, ctrl_cum, ctrl_rate)
                if rec is not None:
                    if is_initial:
                        _write_initial_row(ws, rrow, f, point_no if is_main else "", rec)
                    else:
                        _write_data_row(ws, rrow, f, point_no if is_main else "", rec, prev_cum_of=None)
                else:
                    # 该点该分量当日无记录 → 清掉母版自带旧值/公式
                    _clear_data_cells(ws, rrow, f)
                    if is_main:
                        # 点号：本项目有的点保留（当日缺测而已）；本项目没这个点 → 清掉标签
                        _set(ws, rrow, f.get("point_col"), point_no if point_no in alive else None)

        # ── 母版数据区里**没有点号的预留空行**也要重写 ──
        # 实测十号母版：竖向位移表 7~18 行是点位行（JJC1~JJC12），19~26 行是空着的预留行，
        # 但**每一行都印着 30/10**。`_master_point_blocks` 认点号、扫到第一个空点号就停，
        # 于是这 8 行从没被写过 —— 导出件里同一列上半是规则里的 35/5、下半还是母版的 30/10
        # （用户 2026-10-16 报「一半对得上一半对不上」）。
        # 控制值是**整张表共同的设计限值**，跟这一行有没有点号无关 → 数据区每一行都按规则写，
        # 直到「变化量正负号说明」那一块为止（`tail_start_row` 就是那条界线）。
        from backend.reports.master_expand import tail_start_row

        last_row = max((r for _, rs in blocks for r in rs), default=start - 1)
        for rr in range(last_row + 1, tail_start_row(ws, f.get("point_col") or 1, last_row)):
            _write_control_values(ws, rr, f, ctrl_cum, ctrl_rate)

        # ── 最后收拾一眼：列宽按内容（只加宽，表头有自动换行不参与）──
        # 数值被列宽挤掉才是"看不清"；母版刻意设过的宽度一律不动（版式是它的决定）。
        _autofit_data_area(ws, blocks)

    return summary


def _autofit_data_area(ws, blocks: list) -> None:
    """把数据表 A~L 列按数值内容调宽（只加宽）—— 见 `reports/export_format.py`。"""
    from backend.reports.export_format import autofit_numeric_columns

    rows = [5, 6] + [r for _, rs in blocks for r in rs]  # 表头两行 + 所有点位行
    autofit_numeric_columns(ws, rows, list(range(1, 13)))


def _write_control_values(ws, row: int, f: dict, cum: float | None, rate: float | None) -> None:
    """写 I/J「控制值·累计变化量 / 变化速率」—— 数据区**每一行**都按规则重写。

    调用方覆盖两类行：`_master_point_blocks` 认出的点位行（按点号，扫到空点号即停），
    以及它停住之后、说明行之前的**母版预留空白行**（母版每一行都印着控制值，漏掉就是同一列两个数）。

    None（只填了另一项）→ 那一格**清空**，不沿用母版原值：
    母版上印的 30/10 是别的基坑那一期的限值，留着它 = 文档上出现一个系统里查不到的数。
    （一类观测两个控制值都没配时，整份文档已经在 `ensure_control_rules` 被拦下，走不到这儿。）
    """
    _set_num(ws, row, f.get("control_cum"), _ctl(cum) if cum is not None else None)
    _set_num(ws, row, f.get("control_rate"), _ctl(rate) if rate is not None else None)


def _ctl(v) -> float | int:
    """控制值按整数写（30，而不是 30.0）—— 现场报表上就是整数，别让格式看着别扭。"""
    f = float(v)
    return int(f) if f.is_integer() else round(f, 3)


def _expand_blocks(ws, f: dict, kind: str, blocks: list, db: Session, project_id: int) -> tuple[list, list[str], int]:
    """在母版点位行之后，把项目里多出来的测点补成新的点位行块。

    返回 (新的 blocks, 补进去的点号, 下移了几行)。
    """
    from backend.reports.master_expand import (copy_row_style, shift_rows_down, tail_start_row,
                                               vertical_merge_of)
    from backend.services.point_service import list_points

    rows_per_block = 2 if kind == "displacement" else 1
    have = {b[0] for b in blocks}
    want = sorted(
        (p.point_no for p in list_points(db, project_id, include_inactive=False)
         if (p.point_type == "both" or p.point_type == kind) and p.point_no not in have),
        key=_no_key,   # 自然序：JJC2 在 JJC10 前面（否则字符串序会把 10 排到 2 前）
    )
    if not want:
        return blocks, [], 0

    last_row = blocks[-1][1][-1] if blocks else (f.get("data_start_row") or 7) - 1
    pc = f.get("point_col") or 1
    tail = tail_start_row(ws, pc, last_row)          # 说明/结论/签字块的起始行
    spare = max(0, tail - last_row - 1)              # 母版预留的空白点位行数
    need = len(want) * rows_per_block
    shifted = max(0, need - spare)
    if shifted:
        shift_rows_down(ws, tail, shifted)

    proto = blocks[-1][1][-1] if blocks else last_row   # 行格式样板 = 最后一个母版点位行
    # 母版若把点号列纵向合并（位移表 X/Y 两行共用点号），新块也要照做，否则点号会偏在上一行
    vspan = vertical_merge_of(ws, proto, pc) if rows_per_block > 1 else None
    r = last_row + 1
    for pno in want:
        new_rows = list(range(r, r + rows_per_block))
        for rr in new_rows:
            copy_row_style(ws, proto, rr, ws.max_column or 1)
            # I/J 控制值**不从母版那一行抄**：补出来的行和固有行一样，
            # 随后由 `_write_control_values` 按项目规则逐行写（这里是清格式留空）。
            for key in ("control_cum", "control_rate"):
                _set_num(ws, rr, f.get(key), None)
        if vspan and new_rows[-1] > new_rows[0]:
            ws.merge_cells(start_row=new_rows[0], start_column=pc,
                           end_row=new_rows[-1], end_column=pc)
        blocks = blocks + [(pno, new_rows)]
        r += rows_per_block
    return blocks, want, shifted


def _no_key(no: str):
    m = re.search(r"(\d+)$", no)
    return (0, int(m.group(1))) if m else (1, no)


def _clear_cells(ws, row: int, f: dict, cols: list[str]) -> None:
    """把指定列清空（写 None），用于初始值期清除母版自带旧值/公式。

    注意：必须用 ws.cell(...).value = None 赋值——openpyxl 的
    ws.cell(value=None) 因内部 `if value is not None` 不会清空已有值。
    合并单元格（MergedCell）只读，跳过。
    """
    from openpyxl.cell.cell import MergedCell

    from backend.reports.export_format import (
        GENERAL,
        MM_DELTA_FORMAT,
        autofit_numeric_columns,
        set_number_format,
    )

    for key in cols:
        col = f.get(key)
        if col:
            cell = ws.cell(row=row, column=col)
            if isinstance(cell, MergedCell):
                continue
            cell.value = None
            # 数值列顺手把显示格式归位（母版残留的 0.00000 会让以后手填的数也带 5 位小数）
            if key in _MM_DELTA_KEYS:
                set_number_format(ws, row, col, MM_DELTA_FORMAT)
            elif key in _NUMERIC_KEYS:
                set_number_format(ws, row, col, GENERAL)


# 报表里"写数值"的列（见 _set_num 的说明）—— 清空时也要把显示格式归位
_NUMERIC_KEYS = frozenset(
    {"v0_m", "prev_m", "current_m", "prev_cum_mm", "cum_mm", "delta_mm", "rate",
     "control_cum", "control_rate"}
)
# 其中这几列**要小数点**（毫米读数，整数也显示成 2.0）
_MM_DELTA_KEYS = frozenset({"prev_cum_mm", "cum_mm", "delta_mm", "rate"})


def _clear_data_cells(ws, row: int, f: dict) -> None:
    """清空一行除点号与 I/J 控制值外的全部数据列（B 初始值、C~H 读数/累计/变化/速率、K 预警）。

    用途：母版某点位行在目标日期无观测记录时，擦掉母版自带的旧值/旧公式，
    防止上一期数据（如残留的速率公式 G/2 及其结果）串入本报表。
    """
    _clear_cells(ws, row, f, ["v0_m", "prev_m", "current_m", "prev_cum_mm", "cum_mm", "delta_mm", "rate", "warning_level"])


def _master_point_blocks(ws, start: int, f: dict, is_displacement: bool) -> list[tuple[str, list[int]]]:
    """按母版固有行位解析数据区点位块。

    返回 [(point_no, [行...])]：settlement 每点 1 行（A 有点号）；
    displacement 每点 2 行（首行 A=点号，次行 A 空 为 Y 行）。
    停下的条件只是**第一个空点号**，所以它认不全数据区：母版下面常有几行没点号的预留空行
    （实测十号母版 8 行，且每行印着旧控制值）—— 那部分由 `fill_data_sheets` 补写到说明行为止。
    """
    pc = f.get("point_col") or 1
    end = min(ws.max_row or start, start + 60)
    blocks: list[tuple[str, list[int]]] = []
    r = start
    while r <= end:
        a = ws.cell(row=r, column=pc).value
        if a is None or not str(a).strip():
            break  # 行首无点号 → 数据区结束
        point_no = str(a).strip()
        rows = [r]
        if is_displacement:
            r2 = r + 1
            if r2 <= end:
                a2 = ws.cell(row=r2, column=pc).value
                if a2 is None or not str(a2).strip():
                    rows.append(r2)  # Y 行（点号列空）
        blocks.append((point_no, rows))
        r += len(rows)
    return blocks


def _write_initial_row(ws, row: int, f: dict, point_no: str, rec: dict) -> None:
    """初始值期（第 0 期）写一行：只写 点号 + B 初始值，其余累计/变化/速率/预警列清空。

    母版通常是"已填好的某一期"文件，数据区自带旧值/公式（上次测值、累计公式、
    上次时间等）。初始值期只有初始值，这些旧内容必须清掉，否则串入报表。
    """
    _set(ws, row, f.get("point_col"), point_no or None)
    _set_num(ws, row, f.get("v0_m"), round_m(rec.get("v0_m")))
    # C 上次测值 / D 本次测值 / E 上次累计 / F 本次累计 / G 本次变化 / H 速率 / K 预警 全部清空
    _clear_cells(ws, row, f, ["prev_m", "current_m", "prev_cum_mm", "cum_mm", "delta_mm", "rate", "warning_level"])


def _col_of(cell: str) -> int | None:
    letters = "".join(ch for ch in cell if ch.isalpha())
    from openpyxl.utils import column_index_from_string

    try:
        return column_index_from_string(letters)
    except Exception:
        return None


def _write_data_row(ws, row: int, f: dict, point_no: str, rec: dict, prev_cum_of: None) -> None:
    """写一行数据到模板。读值 m（模板口径），累计/变化/速率 mm —— 全数值覆盖公式。"""
    from backend.reports.export_format import MM_DELTA_FORMAT

    _set(ws, row, f.get("point_col"), point_no or None)
    _set_num(ws, row, f.get("v0_m"), round_m(rec.get("v0_m")))
    _set_num(ws, row, f.get("prev_m"), round_m(rec.get("prev_m")))
    _set_num(ws, row, f.get("current_m"), round_m(rec.get("current_m")))
    # 上次累计 E / 本次累计 F / 本次变化 G / 速率 H —— 写数值覆盖母版公式。
    # 值已在 `paper_values` 收到纸上口径（米列 4 位 = 毫米列 1 位），这里再兜一道，
    # 保证任何来源的 rec 写进纸上的都是同一口径：(D−C)×1000 恒等于 G。
    # 这四列用 MM_DELTA_FORMAT：毫米读数要能看出小数点（整数也显示 2.0，用户 2026-10-08 要求）
    _set_num(ws, row, f.get("prev_cum_mm"), round_mm(rec.get("prev_cum_mm")), MM_DELTA_FORMAT)
    _set_num(ws, row, f.get("cum_mm"), round_mm(rec.get("cum_mm")), MM_DELTA_FORMAT)
    _set_num(ws, row, f.get("delta_mm"), round_mm(rec.get("delta_mm")), MM_DELTA_FORMAT)
    _set_num(ws, row, f.get("rate"), round_rate(rec.get("rate")), MM_DELTA_FORMAT)
    # 预警等级：默认**不写进导出报表**（用户要求"导出的 Excel 不要显示预警"）。
    # ⚠ 这里只清空 —— 母版自带的上一期预警文字也必须擦掉，否则会串进本报表。
    #   绝不能改成"统一写正常"：真有报警却报正常，属于伪造监测数据。
    # 需要恢复时改 backend/reports/export_options.py 一处即可。
    _set(
        ws, row, f.get("warning_level"),
        (_warning_label(rec.get("warning_level")) or None) if EXPORT_SHOW_WARNING_LEVEL else None,
    )


def _set(ws, row: int, col: int | None, value) -> None:
    """写单元格；value=None 也生效（清空）。勿用 ws.cell(value=None)。
    合并单元格（MergedCell）只读，跳过。
    """
    from openpyxl.cell.cell import MergedCell

    if col:
        cell = ws.cell(row=row, column=col)
        if isinstance(cell, MergedCell):
            return
        cell.value = value


def _set_num(ws, row: int, col: int | None, value, fmt: str | None = None) -> None:
    """写**数值**格：值 + 显示格式（默认「常规」）。

    母版里残留的 `0.00000_ ` 格式会让 20 显示成 20.00000、3.119 显示成 3.11900
    （用户 2026-10-08 反馈"会有小数点"）。清空时也顺手把格式归位，
    否则以后手工往这格填数又会带上 5 位小数。

    `fmt` 传 `MM_DELTA_FORMAT` 时给"累计/变化/速率"那几列用（它们**要**小数点）。
    """
    from backend.reports.export_format import GENERAL, set_number_format

    _set(ws, row, col, value)
    set_number_format(ws, row, col, fmt or GENERAL)


def _warning_label(level: str | None) -> str:
    return {"alarm": "报警", "control": "预警"}.get(level or "", "")
