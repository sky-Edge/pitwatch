# -*- coding: utf-8 -*-
"""主界面项目列表：列宽之和必须装得下，否则某一列会被压成竖排。

用户 2026-10-17 报的现象：
    「项目名称」列里「六号左边墩基坑」被挤成**一个字一行**。

根因不是样式写错，而是**算术**：
    这张表每一列（除名称外）都写了固定 width，而 `scroll.x` 又被设成
    ≈「固定宽之和」。于是唯一的弹性列（名称）被分到 2 像素 ——
    中文字会逐字换行，看起来就是竖排。

这类问题改一次很容易再犯（加一列、调个宽度就复发），所以钉成守卫：
    ① `scroll.x` ≥ 各列 width 之和 + 勾选列；
    ② 「项目名称」这一列**必须有固定宽度**（它是唯一没有 dataIndex 渲染兜底的列）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "frontend" / "src" / "pages" / "ProjectsPage.tsx"

#: 勾选列（rowSelection）占的宽度。antd 默认 32px，加上单元格左右 padding 约 48。
SELECTION_COL_W = 48


@pytest.fixture(scope="module")
def page_text() -> str:
    assert PAGE.exists(), f"找不到 {PAGE}"
    return PAGE.read_text(encoding="utf-8")


def _columns_block(text: str) -> str:
    """截出 `const columns: ColumnsType<Project> = [ ... ]` 这一段。

    只在这段里找 width —— 页面别处（批量操作条的 Select、弹窗输入框）也有 width，
    不截范围会把它们算进列宽，得出的和虚高。
    """
    start = text.index("const columns: ColumnsType<Project> = [")
    # 从起点往下找第一个"两个空格缩进的 ]" —— 那是数组结束
    rest = text[start:]
    m = re.search(r"\n  \]\n", rest)
    assert m, "没找到 columns 数组的结尾"
    return rest[: m.start()]


def test_name_column_has_fixed_width(page_text: str) -> None:
    """「项目名称」必须有固定宽度。

    它没有别的列那样的"短内容"兜底，不给宽就会被分到剩余空间；
    而剩余空间 = scroll.x − 其它列固定宽之和，正好可能是 0。
    """
    block = _columns_block(page_text)
    m = re.search(r"dataIndex: 'name',\s*\n\s*width: (\d+),", block)
    assert m, (
        "「项目名称」列没有固定宽度 —— 其余列都是固定宽时，它会被压到几像素，"
        "中文会一个字一行地竖排（2026-10-17 出过这个事故）"
    )
    assert int(m.group(1)) >= 160, f"项目名称列只有 {m.group(1)}px，放不下 10 个字以上的项目名"


def test_scroll_x_covers_all_fixed_widths(page_text: str) -> None:
    """scroll.x 必须 ≥ 各列宽之和 + 勾选列，否则最宽那列会被压扁。"""
    block = _columns_block(page_text)
    widths = [int(x) for x in re.findall(r"^\s+width: (\d+),", block, flags=re.M)]
    assert len(widths) >= 8, f"只解析到 {len(widths)} 个列宽，解析规则可能过时了：{widths}"

    m = re.search(r"scroll=\{\{ x: (\d+) \}\}", page_text)
    assert m, "没找到 scroll={{ x: N }}"
    scroll_x = int(m.group(1))

    need = sum(widths) + SELECTION_COL_W
    assert scroll_x >= need, (
        f"scroll.x={scroll_x} 小于各列宽之和 {sum(widths)} + 勾选列 {SELECTION_COL_W} = {need}；"
        "差出来的宽度会从弹性列里扣，扣光了就变成竖排"
    )


def test_ellipsis_class_exists_for_long_names(page_text: str) -> None:
    """长项目名要一行省略 + 悬停看全名，不能靠折行。"""
    css = (ROOT / "frontend" / "src" / "styles" / "global.css").read_text(encoding="utf-8")
    assert ".cell-ellipsis" in css, "少了 .cell-ellipsis 样式（长名字会撑破或折行）"
    body = css.split(".cell-ellipsis", 1)[1].split("}", 1)[0]
    for prop in ("overflow: hidden", "text-overflow: ellipsis", "white-space: nowrap"):
        assert prop in body, f".cell-ellipsis 缺 {prop}"
    assert "cell-ellipsis" in _columns_block(page_text), "表里没用上 .cell-ellipsis"
