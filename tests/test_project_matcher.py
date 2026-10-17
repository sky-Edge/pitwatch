# -*- coding: utf-8 -*-
"""智能认项目：宽松要宽得住，但**绝不能认错**。

用户 2026-10-13：
    「检查时 基坑名字和系统中项目名字不一定完全对得上 加一个智能识别 不要太死板」

这个文件守两件事：
1. 该认出来的都得认出来（带地名前缀 / 中文数字 / 括号 / 老母版只有编号的文件名）；
2. 不该认的一个都不许认（编号不同、实体不同、两个项目都像 → 一律不认）。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.services.project_matcher import (
    CLOSE_SCORE,
    MIN_SCORE,
    match_project,
    score,
)


@dataclass
class P:
    id: int
    code: str
    name: str


PORTFOLIO = [
    P(1, "PRJ-01", "示例公路一号基坑"),
    P(2, "PRJ-08", "八号基坑"),
    P(3, "PRJ-08D", "八号桥墩"),
    P(4, "PRJ-10", "十号基坑"),
    P(8, "PRJ-12", "十二号基坑"),
]


def _m(text: str, *, src: str = "表内名称", projects=None):
    return match_project(projects or PORTFOLIO, [(text, src)])


# ───────────────────── 一、该认出来的（名字对不上也要认） ─────────────────────


@pytest.mark.parametrize(
    "text, want",
    [
        ("八号基坑", "八号基坑"),
        ("8号基坑", "八号基坑"),  # 阿拉伯数字 ↔ 中文数字
        ("八号基坑（右幅）", "八号基坑"),  # 括号里是限定词
        ("示例公路 八号基坑围护工程", "八号基坑"),  # 地名前缀 + 工程类别后缀
        ("示例公路工程8号基坑围护结构监测", "八号基坑"),  # 前缀 + 后缀 + 换数字写法
        ("八号基坑围护工程监测日报（第3期）", "八号基坑"),  # 期次是噪音
        ("12号基坑", "十二号基坑"),  # 十二 ↔ 12
    ],
)
def test_names_that_must_be_recognized(text: str, want: str) -> None:
    m = _m(text)
    assert m.project is not None, f"「{text}」没认出来：{m.reason}"
    assert m.project.name == want, f"「{text}」认成了 {m.project.name}"


def test_full_width_and_spaces_dont_matter() -> None:
    """全角数字/括号、多余空格都不该影响判断。"""
    assert _m("８号基坑").project.name == "八号基坑"
    assert _m("  八号 基坑 ").project.name == "八号基坑"


def test_old_master_filename_with_code_only() -> None:
    """老母版导出的文件名只有编号、表里也没写名称 —— 按编号认。"""
    m = _m("PRJ-08_日报_2026.08.01.xlsx", src="文件名")
    assert m.project is not None and m.project.name == "八号基坑"
    assert "编号" in m.how


def test_filename_with_project_name() -> None:
    m = _m("十号基坑第3期2026.08.01.xlsx", src="文件名")
    assert m.project is not None and m.project.name == "十号基坑"


def test_filename_paren_content_is_ignored() -> None:
    """文件名括号里常写**母版名**，而母版可能是别的基坑的 —— 括号内容不能参与认项目。

    ⚠ 这个断言得挑干净：文件名里**不能带编号**，否则编号匹配会先把正确答案捞出来，
    括号内容认错也测不出来（第一版就是这么写的，变异检验一试 —— 把括号规则删掉
    测试照样全绿，等于没守）。
    """
    m = _m("十号基坑监测日报（八号基坑母版）.xlsx", src="文件名")
    assert m.project is not None, f"认不出：{m.reason}"
    assert m.project.name == "十号基坑", f"被括号里的母版名带跑了：{m.project.name}"

    # 括号里那个名字本身不该被当成候选文本用
    only_paren = _m("监测日报（八号基坑母版）.xlsx", src="文件名")
    assert only_paren.project is None, "括号里的母版名被当成项目名认了"


# ───────────────────── 二、绝不能认错的 ─────────────────────


def test_different_number_is_never_matched() -> None:
    """编号不同 → 肯定不是同一个基坑（哪怕只差一个字）。"""
    m = _m("五号基坑")
    assert m.project is None, f"五号基坑认成了 {m.project.name}"
    assert score("五号基坑", "八号基坑") == 0.0
    assert score("九号基坑", "八号基坑") == 0.0


def test_different_entity_is_never_matched() -> None:
    """实体不同 → "八号基坑"不是"八号桥墩"。"""
    assert score("八号基坑", "八号桥墩") == 0.0
    assert score("八号隧道", "八号基坑") == 0.0
    # 两边都写"八号"，但要各归各的
    assert _m("8号墩").project.name == "八号桥墩"
    assert _m("八号基坑").project.name == "八号基坑"


def test_unrelated_name_is_not_matched() -> None:
    m = _m("三号隧道")
    assert m.project is None


def test_close_but_insufficient_similarity_is_not_matched() -> None:
    """只有几个字一样（甲基坑 vs 乙基坑）→ 不认。"""
    assert score("甲基坑", "乙基坑") < MIN_SCORE
    assert _m("甲基坑").project is None


# ───────────────────── 三、分不清就不猜 ─────────────────────


def test_two_similar_projects_are_not_guessed() -> None:
    """两个项目都像 → 不认，并把候选摆出来让用户自己定。"""
    two = [P(1, "PRJ-A", "八号基坑"), P(2, "PRJ-B", "八号基坑东侧")]
    m = match_project(two, [("八号基坑", "表内名称")])
    # 完全一致那个应该胜出（另一个只是包含关系，分数更低）
    assert m.project is not None and m.project.code == "PRJ-A"

    tie = [P(1, "PRJ-A", "八号基坑"), P(2, "PRJ-B", "八号基坑")]
    m2 = match_project(tie, [("8号基坑围护工程", "表内名称")])
    assert m2.project is None and m2.ambiguous
    assert len(m2.candidates) == 2
    # 同名时要把编号带出来，不然用户看不出是哪两个
    assert "PRJ-A" in m2.reason and "PRJ-B" in m2.reason


def test_conflicting_names_in_one_report_are_not_guessed() -> None:
    """一份表里同时出现两个项目的名字 → 不认，让用户定。

    这不是假想：母版常是**别的基坑那期另存的**，表里会残留旧工程名。
    要是"先遍历到谁就认谁"，结果就被项目顺序左右了 —— 那才叫真认错。
    """
    m = match_project(PORTFOLIO, [("八号基坑", "表内名称"), ("十号基坑", "表内名称")])
    assert m.project is None, f"表里两个名字，硬认成了 {m.project.name}"
    assert m.ambiguous
    assert {c.project.name for c in m.candidates} >= {"八号基坑", "十号基坑"}


def test_name_wins_over_filename_but_both_are_scored() -> None:
    """表内名称和文件名指向同一个项目 → 正常认出来；指向不同项目 → 分不清。"""
    same = match_project(PORTFOLIO, [("八号基坑", "表内名称"), ("PRJ-08_日报.xlsx", "文件名")])
    assert same.project is not None and same.project.name == "八号基坑"
    assert same.score >= 1.0

    conflict = match_project(PORTFOLIO, [("八号基坑", "表内名称"), ("PRJ-10_日报.xlsx", "文件名")])
    assert conflict.project is None and conflict.ambiguous, "表里名字和文件名指向两个项目，不该硬选"


def test_no_candidate_says_so() -> None:
    """全被否决（编号/实体都对不上）→ 不硬凑候选，直说对不上。

    这里**故意不给候选**：分数 0 的意思是"肯定不是"，把这种项目列成"最像的"
    反而会诱导用户去选错的。
    """
    projects = [P(1, "PRJ-01", "一号基坑")]
    m = match_project(projects, [("三号隧道", "表内名称")])
    assert m.project is None and m.candidates == []
    assert "对得上" in m.reason

    m2 = match_project(projects, [])
    assert m2.project is None and m2.reason


def test_vetoed_names_still_offer_a_rough_reference() -> None:
    """编号/实体对不上 → 不认，但要把"最接近的"摆出来（标成仅作参考）。"""
    m = _m("九号基坑")
    assert m.project is None
    assert m.candidates, "认不出时至少要告诉用户系统里最接近的是哪个"
    assert m.candidates[0].project.name == "八号基坑"
    assert m.candidates[0].weak is True, "参考项必须标成 weak —— 它不能参与认定"
    assert m.candidates[0].to_dict()["weak"] is True


def test_too_far_names_still_list_candidates() -> None:
    """有几分像、但不够 → 把候选列出来，用户一眼知道该怎么改表头。"""
    m = _m("甲基坑")
    assert m.project is None and m.candidates
    assert "不太像" in m.reason


# ───────────────────── 四、编号的近似陷阱 ─────────────────────


def test_shorter_code_does_not_eat_longer_one() -> None:
    """编号只差一位：PRJ-1 不能吃掉 PRJ-10 的报表。"""
    only_one = [P(6, "PRJ-1", "一号互通")]
    m = match_project(only_one, [("PRJ-10_日报_2026.08.01.xlsx", "文件名")])
    assert m.project is None, "PRJ-1 把 PRJ-10 的日报认走了"

    both = [P(6, "PRJ-1", "一号互通"), P(7, "PRJ-10", "十号互通")]
    m2 = match_project(both, [("PRJ-10_日报_2026.08.01.xlsx", "文件名")])
    assert m2.project is not None and m2.project.code == "PRJ-10"


def test_one_char_code_is_not_used() -> None:
    """"1" 这种编号会匹配到一大堆无关文字，不参与认项目。"""
    m = match_project([P(9, "1", "一号墩")], [("第1批报表汇总.xlsx", "文件名")])
    assert m.project is None


# ───────────────────── 五、"只是相近"要能标记出来 ─────────────────────


def test_close_match_is_flagged_for_confirmation() -> None:
    """认出来了但不是完全一致 → is_close，结果页会提醒用户看一眼。"""
    exact = _m("八号基坑")
    assert not exact.is_close, "完全一致不该提示确认"

    near = _m("示例公路工程8号基坑围护结构监测")
    assert near.project is not None and near.is_close
    assert near.score >= MIN_SCORE and near.score < CLOSE_SCORE
    assert "第" not in near.how  # 措辞是中文说明，不是内部名字


def test_reason_and_how_are_human_readable() -> None:
    """给用户看的句子不能出现内部字段名。"""
    m = _m("八号基坑围护工程")
    assert "表内名称" in m.how or "文件名" in m.how
    bad = _m("三号隧道")
    assert "认不出" not in bad.reason  # service 层才拼"认不出是哪个项目 —— "
    assert "不敢替你定" in bad.reason or "沾点边" in bad.reason
