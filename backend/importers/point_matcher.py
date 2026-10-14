# -*- coding: utf-8 -*-
"""测点名称规范化 / 匹配（导入映射用，需求②）。

问题场景：Excel 里的点号（JJC-01 / 1# / jjc1）与项目内已有测点编号写法不一致，
精确匹配失败导致整行无法导入。

方案：
- normalize_point_no：规范化（忽略大小写/全半角/分隔符/前导零）用于模糊命中判断；
- suggest_match：对 Excel 点号给出现有测点的候选匹配（含置信度）；
- 全部为纯函数，可单测。
"""
from __future__ import annotations

import re
import unicodedata

_SEP_RE = re.compile(r"[-_./\s]")


def normalize_point_no(name: str) -> str:
    """规范化点号：全角→半角、去分隔符与空白、大写；字母后前导零不处理（JJC-01 vs JJC1 需数字尾对齐处理）。

    例：normalize("jjc-01") = "JJC01"; normalize("JJC1") = "JJC1";
        normalize("1＃") = "1"（全角#剥除）。
    """
    if not name:
        return ""
    # 全角转半角
    half = unicodedata.normalize("NFKC", name)
    # 去分隔符/空白/# 号
    stripped = _SEP_RE.sub("", half).replace("#", "")
    return stripped.upper()


def _num_tail(name: str) -> tuple[str, str]:
    """拆出前缀与数字尾：'JJC-01' → ('JJC', '01')。无数字尾时 whole=前缀。"""
    m = re.search(r"(\d+)\s*$", name)
    if m:
        return name[: m.start()].upper(), m.group(1)
    return name.upper(), ""


def suggest_match(excel_no: str, existing_nos: list[str]) -> list[dict]:
    """对 Excel 点号在现有测点编号中找候选匹配（按置信度降序，最多 3 个）。

    返回 [{"point_no": 现有编号, "confidence": "exact"|"strong"|"weak"}]
    - exact:  规范化后完全相等（含数字尾对齐前导零：JJC-01→JJC01==JJC01）
    - strong: 前缀相等且数字尾数值相等（JJC-1 == JJC01；或 1# → JJC1 前缀含于）
    - weak:   仅数字尾相等
    """
    if not excel_no:
        return []
    norm = normalize_point_no(excel_no)
    pre, tail = _num_tail(norm)

    exact: list[str] = []
    strong: list[str] = []
    weak: list[str] = []

    for cand in existing_nos:
        c_norm = normalize_point_no(cand)
        if c_norm == norm:
            exact.append(cand)
            continue
        c_pre, c_tail = _num_tail(c_norm)
        if tail and c_tail and tail.lstrip("0") == c_tail.lstrip("0") and (pre and (pre in c_pre or c_pre in pre)):
            strong.append(cand)
            continue
        if tail and c_tail and tail.lstrip("0") == c_tail.lstrip("0"):
            weak.append(cand)

    out: list[dict] = []
    for no in exact:
        out.append({"point_no": no, "confidence": "exact"})
    for no in strong:
        out.append({"point_no": no, "confidence": "strong"})
    for no in weak[:3]:
        out.append({"point_no": no, "confidence": "weak"})
    return out[:3]
