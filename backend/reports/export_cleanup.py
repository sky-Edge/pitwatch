# -*- coding: utf-8 -*-
"""导出前的「母版旧内容清理」—— 把不属于本项目的残留内容从母版副本里擦掉。

【为什么需要】母版是拿**别的基坑**的报表当底模的（用户上传的是"4号右边墩基坑"的整卷），
里面除了版式，还带着那个基坑的**旧期数据**。系统只填"变量槽"（日期/期次/数据表数值），
其余内容原样保留 —— 于是你的报表里会冒出别人的点号和别人的预警等级：

    预警统计!D30 = "橙色预警"   B30 = "地表沉降监测"  C30 = "DBC05-01"
    预警统计!D31 = "黄色预警"   B31 = "管线竖向位移监测" C31 = "GGC02-03"

用户 2026-09-30 的原话是「导出的 Excel 不要显示预警」—— 指的就是这个（他打开文件看到
一堆不属于自己基坑的"黄色预警/橙色预警"）。

【清理原则】**保留结构、清数据**：
· 保留：标题行（"最大变化量统计表""本期预警点统计表"）与表头行 → 看起来是一张标准的空表；
· 清掉：表头下面的数据行，以及标题行旁边挂着的上一期日期；
· 不动：没有"统计表"标题的 Sheet（封面/签字/概况/分析/成果提交…）一个格都不碰。
"""
from __future__ import annotations

from datetime import date

from openpyxl.cell.cell import MergedCell

_STAT_TITLE_KEY = "统计表"
_SCAN_LIMIT = 400      # 统计表扫描的最大行数（母版实测 146 行）


def _row_has_text(ws, row: int, needle: str) -> bool:
    for c in range(1, (ws.max_column or 1) + 1):
        if needle in str(ws.cell(row=row, column=c).value or ""):
            return True
    return False


def _is_stat_data_row(ws, row: int) -> bool:
    """统计表里的**数据行**：B 列是测项名（"地表沉降监测""管线竖向位移监测"…）。

    实测母版所有数据行的测项名都以"监测"结尾，而标题行（"…统计表"）与
    表头行（"测  项 / 测 点 / 等级"）都不以此结尾 —— 这是能把两者分开的判据。
    """
    return str(ws.cell(row=row, column=2).value or "").strip().endswith("监测")


def stat_table_data_ranges(ws) -> list[tuple[int, int]]:
    """母版里各「××统计表」的**数据区行范围**：[(起始行, 结束行), ...]。

    ⚠ 表头行不能用"含『测』"来找：数据行的测项名（"地表沉降监测"）本身就含"测"，
    那样会把表头一路扩进数据区（实测首版就是这样，一个格都没清掉）。
    改用**第一个数据行**做分界：数据区 = 第一个数据行 → 下一个标题行之前。
    """
    top = min(ws.max_row or 1, _SCAN_LIMIT)
    titles = [r for r in range(1, top + 1) if _row_has_text(ws, r, _STAT_TITLE_KEY)]
    out: list[tuple[int, int]] = []
    for i, t in enumerate(titles):
        nxt = titles[i + 1] if i + 1 < len(titles) else top + 1
        start = next((r for r in range(t + 1, min(nxt, t + 5)) if _is_stat_data_row(ws, r)), None)
        if start is not None and start < nxt:
            out.append((start, nxt - 1))
    return out


def clear_stale_stat_tables(wb) -> dict:
    """清掉各「××统计表」里的旧期数据，返回 {sheet: 清掉的格数}（0 格的不返回）。

    母版路径与旧式模板路径共用 —— 两条路径都是从母版原件复制的，都会有这个残留。
    """
    cleared: dict[str, int] = {}
    for ws in wb.worksheets:
        ranges = stat_table_data_ranges(ws)
        if not ranges:
            continue
        n = 0
        in_data = {r for r0, r1 in ranges for r in range(r0, r1 + 1)}
        for r in range(1, (ws.max_row or 1) + 1):
            for c in range(1, (ws.max_column or 1) + 1):
                cell = ws.cell(row=r, column=c)
                if isinstance(cell, MergedCell) or cell.value is None:
                    continue
                # 数据区里的一切；数据区之外只清**日期型**（标题行旁边挂的上一期日期）
                if r in in_data or isinstance(cell.value, date):
                    cell.value = None
                    n += 1
        if n:
            cleared[ws.title] = n
    return cleared
