# -*- coding: utf-8 -*-
"""母版学习：从一份已填好的整卷样板中识别【每期变量槽】。

识别目标（第 15 vs 21 期逐格比对已锁定 = 变量全集）：
- 封面：日期格（Excel 序列号，如 F16）、工程名格、基坑名格（E11/F11 标签+值）
- 签字：期次格（含"第X期"文本，如 A11）、日期格（E52）
- 数据 Sheet（竖向位移 / r.立柱结构水平位移）：
    - 表头行（含"点号"）、数据起始行
    - 点号列 A；初始值 B；C=上次测值 D=本次测值 E=上次累计 F=本次累计 G=本次变化 H=速率
    - I/J 控制值（母版固定，不写）；K 预警等级（可写）；L 备注
    - 天气格（"天气："标签右侧格，如 K2）
- 日期序列号 → 日期：Excel 序列 46254 ≈ 2026-08-20（1900 系统）

学习结果存入 ExportTemplate.layout_json，供每期渲染时填充。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from openpyxl import load_workbook

_SHEET_HINTS = {
    "竖向位移": "settlement",
    "水平位移": "displacement",
}

# 期次文本形态：可带全角/半角括号，主体为"第X期"或"初始值/初始"
_PERIOD_RE = re.compile(r"^[（(]?\s*(?:第\s*[0-9一二三四五六七八九十百千〇零两]+\s*期|初始值|初始)\s*[）)]?$")


def is_period_text(t: str) -> bool:
    """单元格文本是否为"整格期次"（第X期 /（第X期）/ 初始值 /（初始值））。

    仅供识别展示，能否作为"每期可写槽位"见 is_period_slot_text。
    """
    compact = t.replace(" ", "").replace("\u3000", "")
    if not compact or len(compact) > 24:
        return False
    if _PERIOD_RE.match(compact):
        return True
    # 兼容历史宽松判据：整格含"第…期"
    return "第" in compact and "期" in compact


def is_period_slot_text(t: str, sheet_name: str = "") -> bool:
    """是否可作为"每期可写槽位"的期次文本。

    - 含"第…期"（第X期 /（第X期））→ 任何页都是期次标识，可每期改写；
    - 仅"初始值/（初始值）"（不含"第"）→ 只在 签字/封面 页视为期次槽，
      避免把概况/分析页里"与初始值对比"这类固定表头误当槽位整卷改写。
    """
    compact = t.replace(" ", "").replace("\u3000", "")
    if not compact or len(compact) > 24:
        return False
    if "第" in compact and "期" in compact:
        return True
    if _PERIOD_RE.match(compact):
        return "签字" in sheet_name or "封面" in sheet_name
    return False


def _col_letter(col: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(col)


def _cell_ref(ws_title: str, row: int, col: int) -> str:
    return f"{ws_title}!{_col_letter(col)}{row}"


def learn_master_layout(path: Path) -> dict:
    """识别母版变量槽。返回 layout dict（meta + sheets + text_blocks）。"""
    wb = load_workbook(path, data_only=True)
    try:
        meta: dict = {}
        sheets_cfg: list[dict] = []
        text_blocks: list[dict] = []

        for ws_title in wb.sheetnames:
            ws = wb[ws_title]
            max_row = ws.max_row or 1
            max_col = ws.max_column or 1

            # ---------- 概况/分析/成果提交类：识别"文字区块"（每期按数据重写）----------
            text_blocks.extend(_learn_text_blocks(ws, ws_title, max_row, max_col))

            # ---------- 封面/签字类：找标签文本定位变量 ----------
            is_disp_sheet = any(h in ws_title for h in ("位移", "沉降", "竖向"))
            # 数据表特征：表头行含"点号"且含"测值"
            header_row = _find_header_row(ws, max_row, max_col)

            if header_row is not None:
                # ---- 数据 Sheet ----
                f = _detect_data_columns(ws, header_row, max_col)
                if f:
                    kind = "settlement" if "竖向" in ws_title or "沉降" in ws_title else "displacement"
                    # 数据起始行 = 表头行下，若下行为第二层表头（含"累计"/"速率"等）再 +1
                    start = header_row + 1
                    sub = [str(ws.cell(row=header_row + 1, column=c).value or "") for c in range(1, max_col + 1)] if header_row + 1 <= max_row else []
                    if any(("累计" in t or "速率" in t or "预警" in t or "控制" in t) for t in sub):
                        start += 1
                    f["data_start_row"] = start
                    # 天气格：扫描行 1-4 找"天气："标签右侧
                    weather_cell = _find_weather_cell(ws, max_col)
                    if weather_cell:
                        f["weather_cell"] = weather_cell
                    sheet_cfg = {"sheet_name": ws_title, "kind": kind, "layout": f, "mode": "x_y_two_rows" if kind == "displacement" else "per_row"}
                    # 原始数据区域（每期同步的 X/Y/Z 点号坐标表）—— 只在母版含此区域时识别
                    raw = _detect_raw_data_block(ws, max_row, max_col)
                    if raw:
                        sheet_cfg["raw_data"] = raw
                    sheets_cfg.append(sheet_cfg)
            else:
                # ---- 封面/签字/其他：扫文本变量槽 ----
                _scan_meta_sheet(ws, ws_title, max_row, max_col, meta)

        # 天气格可能在数据表（竖向位移 K2）—— 上面已抓
        wb.close()
        return {"meta": meta, "sheets": sheets_cfg, "text_blocks": text_blocks}
    except Exception:
        wb.close()
        raise


# ─────────────── 文字区块识别（概况 / 分析 / 成果提交）───────────────
#
# 这三页装的是**整段文字**（不是变量槽），母版里带着别的工程的内容
# （实测：五份母版的这三页逐字完全相同，分析页写的是"地表沉降 DBC05-01""测斜QCX1"，
#  结论页写的是"屿前站" —— 与用户的钢板桩基坑毫无关系）。
# 所以按"锚点标题 → 下方正文区块"把格子记下来，导出时按本期数据重写。
#
# ⚠ 判据刻意保守：**认不出来就不生成**（保持母版原样），绝不猜。

#: (Sheet 名关键词, 锚点关键词, 用途)
_TEXT_ANCHORS: list[tuple[str, str, str]] = [
    ("分析", "现场巡视", "overview"),
    ("分析", "监测成果分析", "analysis_items"),
    ("成果提交", "监测结论", "conclusion"),
    ("成果提交", "监测成果", "deliverables"),
    ("概况", "监测完成情况", "progress"),
]

#: 概况页「监测项目工作量」表的表头关键词 → 列含义
_WORKTABLE_HEADS = (
    ("index", ("序号",)),
    ("item", ("监测项目",)),
    ("week", ("本周完成工作量", "工作量")),
    ("total", ("累积完成工作量", "累计完成工作量")),
    ("note", ("备注",)),
)

_SECTION_TITLE_RE = re.compile(r"^\s*\d+(\.\d+)?\s*[、.．]")


def _is_section_title(text: str) -> bool:
    """"5.2相关建议：" / "6、监测成果" 这类小节标题 —— 区块到此为止（别把下一节吃进来）。"""
    t = text.strip()
    return len(t) <= 40 and bool(_SECTION_TITLE_RE.match(t))


def _cell_text(ws, row: int, col: int) -> str:
    return str(ws.cell(row=row, column=col).value or "").strip()


def _find_anchors(ws, keyword: str, max_row: int, max_col: int) -> list[tuple[int, int]]:
    top = min(max_row, 160)
    right = min(max_col, 12)
    return [
        (r, c)
        for r in range(1, top + 1)
        for c in range(1, right + 1)
        if keyword in _cell_text(ws, r, c)
    ]


def _block_refs_below(ws, anchor_row: int, anchor_col: int, max_row: int, max_col: int) -> list[str]:
    """锚点**下方**的正文区块：先定正文列（锚点列起 3 列内最左的非空格），再逐行收集。

    停止条件：空格 / 出现小节标题 / 碰到表头行（含"序号"）——
    这三个都不会误伤正文段落（正文以"(1)…"开头，不是"5.2…"那种编号标题）。
    """
    limit = min(max_row, anchor_row + 80)
    body_col = None
    first_row = None
    for r in range(anchor_row + 1, limit + 1):
        for c in range(anchor_col, min(anchor_col + 3, max_col) + 1):
            if _cell_text(ws, r, c):
                body_col, first_row = c, r
                break
        if body_col is not None:
            break
    if body_col is None or first_row is None:
        return []
    refs: list[str] = []
    for r in range(first_row, limit + 1):
        t = _cell_text(ws, r, body_col)
        if not t or _is_section_title(t) or "序号" in t:
            break
        refs.append(_cell_ref(ws.title, r, body_col))
    return refs


def _learn_text_blocks(ws, ws_title: str, max_row: int, max_col: int) -> list[dict]:
    """识别该 Sheet 上的文字区块；一个都没认出来就返回空列表（不改母版任何东西）。"""
    out: list[dict] = []
    for sheet_kw, anchor_kw, purpose in _TEXT_ANCHORS:
        if sheet_kw not in ws_title:
            continue
        for row, col in _find_anchors(ws, anchor_kw, max_row, max_col):
            text = _cell_text(ws, row, col)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            idx = next((i for i, ln in enumerate(lines) if anchor_kw in ln), None)
            # 情形一：标题与正文在**同一个格**里（母版的分析页就是这样：C1 含"4.1 现场巡视情况"
            # 及其正文）→ 只改这一格，保留标题行。
            if idx is not None and any(ln.strip() for ln in lines[idx + 1:]):
                out.append(
                    {
                        "sheet": ws_title,
                        "purpose": purpose,
                        "cells": [_cell_ref(ws_title, row, col)],
                        "keep_until_line_containing": anchor_kw,
                    }
                )
                break
            # 情形二：标题是独立一格，正文在其下方
            refs = _block_refs_below(ws, row, col, max_row, max_col)
            if refs:
                out.append({"sheet": ws_title, "purpose": purpose, "cells": refs})
                break
        out.extend(_learn_worktable(ws, ws_title, purpose, max_row, max_col))
    return out


def _learn_worktable(ws, ws_title: str, purpose: str, max_row: int, max_col: int) -> list[dict]:
    """"监测项目工作量"表：表头行 + 数据行范围（母版实测只有 1 行数据行）。"""
    if purpose != "progress":
        return []
    top = min(max_row, 160)
    right = min(max_col, 12)
    for r in range(1, top + 1):
        cols: dict[str, int] = {}
        for key, kws in _WORKTABLE_HEADS:
            for c in range(1, right + 1):
                t = _cell_text(ws, r, c)
                if t and any(k in t for k in kws):
                    cols[key] = c
                    break
        # 至少要认出"监测项目"和"工作量"两列，且下面真有数据行 —— 否则不认（宁可不改）
        if "item" not in cols or "week" not in cols:
            continue
        last = None
        for rr in range(r + 1, min(r + 40, max_row) + 1):
            if _cell_text(ws, rr, cols["item"]):
                last = rr
            else:
                break
        if last:
            return [
                {
                    "sheet": ws_title,
                    "purpose": "worktable",
                    "header_row": r,
                    "first_row": r + 1,
                    "last_row": last,
                    "cols": cols,
                }
            ]
    return []


def _find_header_row(ws, max_row: int, max_col: int) -> int | None:
    for r in range(1, min(max_row, 9) + 1):
        texts = [str(ws.cell(row=r, column=c).value or "") for c in range(1, max_col + 1)]
        if any("点号" in t for t in texts) and any(("测值" in t or "初始值" in t) for t in texts):
            return r
    return None


def _detect_data_columns(ws, header_row: int, max_col: int) -> dict:
    """按表头文本映射列。返回 {point_col, v0_m, prev_m, current_m, prev_cum_mm, cum_mm, delta_mm, rate, warning_level, control_cum, control_rate}"""
    top = [str(ws.cell(row=header_row, column=c).value or "") for c in range(1, max_col + 1)]
    sub_row = header_row + 1
    sub = [str(ws.cell(row=sub_row, column=c).value or "") for c in range(1, max_col + 1)] if sub_row <= (ws.max_row or 1) else []

    def find(*kws, in_row=None) -> int | None:
        row_texts = top if in_row is None else in_row
        for i, t in enumerate(row_texts, start=1):
            if t and all(k in t for k in kws):
                return i
        return None

    def find2(*kws) -> int | None:
        """top 优先，sub 兜底。"""
        c = find(*kws, in_row=top)
        if c is None and sub:
            c = find(*kws, in_row=sub)
        return c

    f = {
        "point_col": find("点号"),
        "v0_m": find2("初始值"),
        "prev_m": find2("上次测值"),
        "current_m": find2("本次测值"),
        "prev_cum_mm": find2("上次累计"),
        "cum_mm": find2("本次累计"),
        "delta_mm": find2("本次变化"),
        "rate": find2("速率"),
        "warning_level": find("预警"),
    }
    # 控制值：top I 列"控制值"合并，R6 I/J 拆"累计变化量/变化速率"
    if find("控制值", in_row=top):
        f["control_cum"] = find("累计", in_row=sub) or find("累计", in_row=top)
        f["control_rate"] = find("速率", in_row=sub) or find("速率", in_row=top)
    return f


def _find_weather_cell(ws, max_col: int) -> str | None:
    for r in range(1, 5):
        for c in range(1, max_col + 1):
            v = str(ws.cell(row=r, column=c).value or "")
            if "天气" in v:
                # 取"天气："后面的值格（同格冒号后或右一格）
                if "：" in v and v.split("：")[-1].strip() or v.split(":")[-1].strip():
                    return _cell_ref(ws.title, r, c)
                if c < max_col:
                    return _cell_ref(ws.title, r, c + 1)
    return None


def _detect_raw_data_block(ws, max_row: int, max_col: int) -> dict | None:
    """探测"原始数据"区：日期 + 点号坐标表。

    形态：某行 X 列 = "日期"、同行 Y 列 = 日期值；某行 X 列 = "点号"、同行 Y 列 = "坐标数据"；
    点号列在 X/Y 两列交错分布，每点 3 行（X/Y/Z），差 3 行。

    返回 {"date_value_cell": str, "left": [...], "right": [...]}；
    左/右每项 {"point_col", "component_col", "value_col", "rows": [r0, r0+3, r0+6, ...]}
    """
    # 1) 找"日期"标签与其右一格的日期值
    date_value_cell = None
    date_row = None
    for r in range(1, min(max_row, 12) + 1):
        for c in range(1, min(max_col, 20) + 1):
            v = str(ws.cell(row=r, column=c).value or "").strip()
            if v == "日期" and c < max_col:
                # 候选日期值格：右一格
                val_cell = ws.cell(row=r, column=c + 1).value
                if val_cell is not None:
                    from openpyxl.utils import get_column_letter
                    date_value_cell = f"{get_column_letter(c + 1)}{r}"
                    date_row = r
                break
        if date_value_cell:
            break
    if not date_value_cell:
        return None

    # 2) 找"点号/坐标数据"表头行（应在日期行附近，差几行）
    head_row = None
    for r in range(date_row + 1, min(max_row, 20) + 1):
        for c in range(1, min(max_col, 20) + 1):
            t = str(ws.cell(row=r, column=c).value or "").strip()
            if t == "点号" and c < max_col:
                neighbor = str(ws.cell(row=r, column=c + 1).value or "").strip()
                if "坐标" in neighbor:
                    head_row = (r, c)  # 行号、点号列
                    break
        if head_row:
            break
    if not head_row:
        return None

    head_r, point_c = head_row
    comp_c = point_c + 1  # "X"/"Y"/"Z" 列
    value_c = comp_c + 1  # 数值列

    # 3) 在 point_c 列（点号列）找所有点号行；每点只在首行出现一次，下方 3 行是 X/Y/Z 三分量
    #    判别：连续 3 行 [点号, 空, 空] 起始该点号；通常起点间距 3
    point_c_rows: list[tuple[int, str]] = []
    for r in range(head_r + 1, min(max_row, 60) + 1):
        v = str(ws.cell(row=r, column=point_c).value or "").strip()
        if v and v not in ("点号", "日期") and not v.startswith("="):
            point_c_rows.append((r, v))
    if not point_c_rows:
        return None

    # 起点推断：取首个出现的点号作为基准，间距 = 第二个 - 第一个；若仅一个点则间距 = 3
    first_r = point_c_rows[0][0]
    if len(point_c_rows) >= 2:
        step = point_c_rows[1][0] - point_c_rows[0][0]
    else:
        step = 3
    left_points = []
    for r, no in point_c_rows:
        # 该点起点 = r；按 step=3 布局 X/Y/Z
        if step == 3 and all(r > head_r for r in (r,)):
            left_points.append((r, no))
        elif step >= 1:
            left_points.append((r, no))
    left_points.sort()
    # 验证右侧列间距（如果只有 step 个点，左列填满 6 个则是另一回事）—— 保留探测到的真实起点即可

    # 4) 右侧点号列：找是否有右列也有"点号/坐标数据"表头
    right_points = []
    right_c = None
    for c2 in range(point_c + 3, min(max_col, 20) + 1):
        t = str(ws.cell(row=head_r, column=c2).value or "").strip()
        neighbor = str(ws.cell(row=head_r, column=c2 + 1).value or "").strip() if c2 < max_col else ""
        if t == "点号" and "坐标" in neighbor:
            right_c = c2
            for r in range(head_r + 1, min(max_row, 60) + 1):
                v = str(ws.cell(row=r, column=c2).value or "").strip()
                if v and v not in ("点号", "日期") and not v.startswith("="):
                    right_points.append((r, v))
            break

    from openpyxl.cell.cell import MergedCell

    def _cell(row, col, sheet_title):
        return _cell_ref(sheet_title, row, col)

    sheet = ws.title
    left_cfg = [
        {"start_row": r, "point_no": n, "point_cell": _cell(r, point_c, sheet), "component_cell": _cell(r, comp_c, sheet), "value_cell": _cell(r, value_c, sheet)}
        for r, n in left_points
    ]
    right_cfg = []
    if right_c is not None:
        right_cfg = [
            {"start_row": r, "point_no": n, "point_cell": _cell(r, right_c, sheet), "component_cell": _cell(r, right_c + 1, sheet), "value_cell": _cell(r, right_c + 2, sheet)}
            for r, n in right_points
        ]

    return {
        "date_value_cell": date_value_cell,
        "head_row": head_r,
        "left": left_cfg,
        "right": right_cfg,
    }


def _scan_meta_sheet(ws, ws_title: str, max_row: int, max_col: int, meta: dict) -> None:
    """在非数据 Sheet 中扫变量槽：期次（第X期 /（初始值））、封面日期、签字日期。

    期次槽允许同时存在多个（封面/签字/其他整卷页均可能带"第X期/（初始值）"文本），
    全部记入 meta["period_cells"]；meta["period_cell"] 保留为签字 sheet 期次格
    （无则取第一个扫描到的）以兼容旧版渲染逻辑。
    """
    date_candidates: list[str] = []
    period_candidates: list[str] = []
    for r in range(1, min(max_row, 60) + 1):
        for c in range(1, min(max_col, 12) + 1):
            v = ws.cell(row=r, column=c).value
            if v is None:
                continue
            t = str(v).strip()
            if not t:
                continue
            ref = _cell_ref(ws_title, r, c)
            # 期次：整格形如"第X期/（第X期）/初始值/（初始值）"（后者仅签字/封面页算）
            if is_period_slot_text(t, ws_title):
                period_candidates.append(ref)
                continue
            # 日期：Excel 序列号数字 (43000~47000) 或 ISO 日期
            if re.fullmatch(r"\d{5}", t) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
                date_candidates.append(ref)

    # 封面日期 = 该 sheet 是封面且数字型；签字日期同理 —— 记录两个候选分别存
    if "封面" in ws_title and date_candidates:
        meta.setdefault("cover_date_cell", date_candidates[0])
    if "签字" in ws_title:
        if period_candidates:
            meta.setdefault("period_cell", period_candidates[0])
        if date_candidates:
            meta.setdefault("sign_date_cell", date_candidates[-1])
    # 期次槽统一入册（封面/签字/其他 sheet 都可能带期次，全部记录）
    if period_candidates:
        cells = meta.setdefault("period_cells", [])
        for ref in period_candidates:
            if ref not in cells:
                cells.append(ref)
    # 兜底：没有封面/签字命名时，第一个含日期数字的 sheet 当封面日期用
    if not meta.get("cover_date_cell") and date_candidates and not meta:
        meta.setdefault("cover_date_cell", date_candidates[0])
    if not meta.get("period_cell") and period_candidates:
        meta.setdefault("period_cell", period_candidates[0])
