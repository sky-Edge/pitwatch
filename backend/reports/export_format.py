# -*- coding: utf-8 -*-
"""导出报表的「数值口径 + 显示格式 + 列宽」收拾（各条导出路径共用）。

**为什么要它**：母版是从真实报表另存来的，里面残留着 Excel 自动加的显示格式。
实测用户那份母版：B~H 各列、水平位移表的 I/J 控制值、以及原始数据区的坐标格，
number_format 全是 `0.00000_ `。系统把引擎算好的数值写进去时**只改值、不改格式**，
于是报表上就出现 `20.00000`、`3413192.92900` 这种尾巴 —— 而母版上印的本来是
`20`、`3413192.929`（用户 2026-10-08 反馈："会有小数点"、"列宽没控制在最佳位置"）。

**三条原则**：
1. **写数值的格子一律设成「常规」(General)**：整数显示整数、小数按实际精度
   （写入前已按下面第 3 条收过口径）。显示格式不该由母版的残留决定。
   **例外**：「上次累计变化量 / 本次累计变化量 / 本次变化量 / 变化速率」这四列用
   `MM_DELTA_FORMAT`（`0.0##`）—— 毫米级读数要能看出小数点，整数也显示成 `2.0`
   （用户 2026-10-08 明确要求"把小数点加上"）。
2. **列宽只加宽、不放窄**：母版的列宽是版式决策（还牵着 A4 打印分页），
   我们只保证写进去的内容放得下，绝不去动它的视觉。
   表头都带自动换行（wrapText），本来就会折行 —— 测宽时**跳过换行格**，
   否则会把表头文字算进去、把列撑得很宽（实测会把 A 列算到 70 宽）。
3. **写进去的数一律先收到「纸上口径」**（见下面 `MM_DECIMALS` / `paper_values`）：
   同一行里米列和毫米列必须能手算互相复核，否则报表自己打自己脸。
"""
from __future__ import annotations

from openpyxl.cell.cell import MergedCell
from openpyxl.styles.numbers import is_date_format
from openpyxl.utils import get_column_letter

GENERAL = "General"
#: 日期槽的显示格式（母版那一格没带日期格式时补上这个，见 `needs_date_format`）
DATE_FORMAT = "yyyy-mm-dd"
# 「累计变化量 / 变化速率」这几列的显示格式（用户 2026-10-08 要求"把小数点加上"）：
#   `0.0##` = 至少 1 位小数（整数也显示成 2.0）、最多 3 位（引擎算的就是 3 位）、
#   多余的零不补（0.5 就是 0.5，不是 0.500）。
# 为什么不干脆固定 2 位：会把 0.125 显示成 0.13，报表读数与系统判定就对不上了。
MM_DELTA_FORMAT = "0.0##"

# ── 报表「纸上口径」：读数(m) 与 累计/变化(mm) 必须能手算互相复核 ──────────────
# 为什么要有这一段：同一行里 B/C/D 印的是**米**、E/F/G 印的是**毫米**，
# 甲方拿到表就会用 (本次−上次)×1000 去核「本次变化量」。原先 m 列 round 到 3 位
# （= 1mm 粒度）、mm 列却直接写引擎原值（0.1mm 甚至更细），于是纸上印出
# 「初始 2.799 / 本次 2.799 / 本次累计 0.4」这种自己打自己脸的数
# （用户 2026-10-16 报"手算对不上"）。两个数各自都"对"，摆在一起就是错的。
#
# 口径：**毫米列 1 位小数（0.1mm —— 就是水准仪/全站仪的实际读数精度），
# 米列 = 毫米列 + 3 位 = 4 位小数**，且**派生量一律由"纸上那个读数"反算**，
# 不再直接抄引擎原值。这样 (D−C)×1000、(D−B)×1000 在纸上恒等成立。
#
# 为什么不反过来"m 保持 3 位、把 mm 取整"：那会把 29.96 印成 30，
# 和控制值 30 并排看着像超限、系统判的却是正常 —— 正是上面 `MM_DELTA_FORMAT`
# 那条注释说的"报表读数与系统判定对不上"。宁可多印一位，不可少印一位。
MM_DECIMALS = 1
M_DECIMALS = MM_DECIMALS + 3
#: 速率 = 变化量 ÷ 天数，除不尽是常态，比变化量多留 2 位（0.001 mm/d）
RATE_DECIMALS = MM_DECIMALS + 2

_PAD = 2.0          # 列宽余量（Excel 一个字符宽 ≈ 1）
MIN_WIDTH = 6.0
MAX_WIDTH = 20.0    # 放不下就让它显示成科学计数/截断，也别把 A4 版式撑破


def _norm(v: float, digits: int) -> float:
    """收到指定小数位，并把 -0.0 归一成 0.0（报表上印出「-0.0」像是数据坏了）。"""
    return round(v, digits) + 0.0


def round_mm(v: float | None) -> float | None:
    """毫米值 → 纸上口径（0.1mm）。"""
    return _norm(float(v), MM_DECIMALS) if v is not None else None


def round_m(v: float | None) -> float | None:
    """米值 → 纸上口径（0.0001m，正好等于 0.1mm）。"""
    return _norm(float(v), M_DECIMALS) if v is not None else None


def round_rate(v: float | None) -> float | None:
    """速率 → 纸上口径（0.001 mm/d）。"""
    return _norm(float(v), RATE_DECIMALS) if v is not None else None


def mm_to_m(v_mm: float | None) -> float | None:
    """mm → 纸上的米值。**mm→m 只此一处**：各条导出路径自己写 `/1000` 的话，
    迟早有一条忘了收口径，纸上就又出现"米列 3 位、毫米列 1 位"对不上的行。"""
    return round_m(v_mm / 1000) if v_mm is not None else None


def paper_values(
    v0_mm: float | None,
    prev_mm: float | None,
    cur_mm: float,
    skipped_days: int | None,
) -> dict:
    """一行的「读数 + 派生量」一次收到纸上口径 —— **各条导出路径共用**。

    派生量（上次累计 / 本次累计 / 本次变化 / 速率）全部由**收过口径的读数**算出来，
    不抄引擎原值：纸上印的是 2.7988 和 2.7984，那「本次变化量」就必须是 0.4，
    而不是引擎那个 0.4000000000000021。`skipped_days` 为空表示首期（无上次可比）。
    """
    v0 = round_mm(v0_mm)
    prev = round_mm(prev_mm)
    cur = round_mm(cur_mm)
    delta = round_mm(cur - prev) if prev is not None else None
    return {
        "v0_m": mm_to_m(v0),
        "prev_m": mm_to_m(prev),
        "current_m": mm_to_m(cur),
        "prev_cum_mm": round_mm(prev - v0) if prev is not None and v0 is not None else None,
        "cum_mm": round_mm(cur - v0) if v0 is not None else None,
        "delta_mm": delta,
        "rate": round_rate(delta / skipped_days) if delta is not None and skipped_days else None,
    }


def needs_date_format(fmt: str | None) -> bool:
    """这一格照现在的格式**显示不出日期**吗？

    日期槽写的是 Excel 序列号（母版上本来就是），能不能看着像日期全看单元格的数字格式。
    十号基坑的母版 `封面!F16` 是 General —— 只写数字，封面就印着 `46311`
    （2026-10-16 界面预览里看见）。自带日期格式的一律不改，那是用户自己的排版。
    """
    return not is_date_format(fmt or GENERAL)


def set_number_format(ws, row: int, col: int | None, fmt: str = GENERAL) -> None:
    """把该格设成指定显示格式（默认「常规」）。合并单元格只读，跳过。"""
    if not col:
        return
    cell = ws.cell(row=row, column=col)
    if isinstance(cell, MergedCell):
        return
    cell.number_format = fmt


def _shown_width(value) -> float:
    """估"照常规格式显示出来占多宽"（中文按 2 个字符宽算）。"""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        f = float(value)
        # 6 位：够盖住纸上口径的 4 位小数米值（2.7988），又能把浮点尾巴
        # （2.7988000000000004）截掉，不然按 3 位测宽会把 D 列少算一个字符、数字被挤掉。
        s = f"{f:.6f}".rstrip("0").rstrip(".")
    else:
        s = str(value)
    return sum(2.0 if ord(ch) > 0x2E80 else 1.0 for ch in s)


def autofit_numeric_columns(ws, rows: list[int], cols: list[int]) -> dict[str, float]:
    """按 `rows` 里**数值格**的内容把 `cols` 调宽到放得下（**只加宽**）。

    只考虑数值格、且跳过自动换行的格子：数值被列宽挤掉是"看不清"，
    文字（表头）有 wrapText 会自己折行、不需要加宽。

    返回 {列字母: 新宽度}（只含真的改宽了的列），便于测试与排查。
    """
    changed: dict[str, float] = {}
    default = ws.sheet_format.defaultColWidth or 8.43
    for col in cols:
        need = 0.0
        for r in rows:
            cell = ws.cell(row=r, column=col)
            if isinstance(cell, MergedCell):
                continue
            v = cell.value
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if cell.alignment is not None and cell.alignment.wrap_text:
                continue
            need = max(need, _shown_width(v))
        if need <= 0:
            continue

        letter = get_column_letter(col)
        # ⚠ 不能直接 ws.column_dimensions[letter]：DimensionHolder 取不存在的键会
        #   **现场创建一个 width=13 的假维度**，于是"没设过宽度的列"被误读成 13 宽、
        #   该加宽的反而不加。必须用 .get() 只读判断。
        dim = ws.column_dimensions.get(letter)
        cur = (dim.width if dim is not None and dim.width else None) or default
        want = min(max(need + _PAD, cur), MAX_WIDTH)
        if want > cur + 0.01:
            ws.column_dimensions[letter].width = want
            changed[letter] = want
    return changed
