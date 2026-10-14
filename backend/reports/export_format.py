# -*- coding: utf-8 -*-
"""导出报表的「数值显示格式 + 列宽」收拾（母版套打用）。

**为什么要它**：母版是从真实报表另存来的，里面残留着 Excel 自动加的显示格式。
实测用户那份母版：B~H 各列、水平位移表的 I/J 控制值、以及原始数据区的坐标格，
number_format 全是 `0.00000_ `。系统把引擎算好的数值写进去时**只改值、不改格式**，
于是报表上就出现 `20.00000`、`3413192.92900` 这种尾巴 —— 而母版上印的本来是
`20`、`3413192.929`（用户 2026-10-08 反馈："会有小数点"、"列宽没控制在最佳位置"）。

**两条原则**：
1. **写数值的格子一律设成「常规」(General)**：整数显示整数、小数按实际精度
   （写入前已 round 到 3 位）。显示格式不该由母版的残留决定。
   **例外**：「上次累计变化量 / 本次累计变化量 / 本次变化量 / 变化速率」这四列用
   `MM_DELTA_FORMAT`（`0.0##`）—— 毫米级读数要能看出小数点，整数也显示成 `2.0`
   （用户 2026-10-08 明确要求"把小数点加上"）。
2. **列宽只加宽、不放窄**：母版的列宽是版式决策（还牵着 A4 打印分页），
   我们只保证写进去的内容放得下，绝不去动它的视觉。
   表头都带自动换行（wrapText），本来就会折行 —— 测宽时**跳过换行格**，
   否则会把表头文字算进去、把列撑得很宽（实测会把 A 列算到 70 宽）。
"""
from __future__ import annotations

from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter

GENERAL = "General"
# 「累计变化量 / 变化速率」这几列的显示格式（用户 2026-10-08 要求"把小数点加上"）：
#   `0.0##` = 至少 1 位小数（整数也显示成 2.0）、最多 3 位（引擎算的就是 3 位）、
#   多余的零不补（0.5 就是 0.5，不是 0.500）。
# 为什么不干脆固定 2 位：会把 0.125 显示成 0.13，报表读数与系统判定就对不上了。
MM_DELTA_FORMAT = "0.0##"
_PAD = 2.0          # 列宽余量（Excel 一个字符宽 ≈ 1）
MIN_WIDTH = 6.0
MAX_WIDTH = 20.0    # 放不下就让它显示成科学计数/截断，也别把 A4 版式撑破


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
        s = str(int(f)) if f.is_integer() else f"{f:.3f}".rstrip("0").rstrip(".")
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
