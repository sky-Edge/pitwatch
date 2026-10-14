# -*- coding: utf-8 -*-
"""回归测试：前端设计令牌（theme/tokens.ts + styles/global.css）不得被绕过。

背景：收口前主色写在 App.tsx、报警红散在 6 个页面、曲线调色板自成一套，
`alarm ? 红 : 金` 这种判断在 4 个文件里各写一遍。这类"样式分散"不会报错，
只会随时间慢慢走偏，所以用测试把约束固定下来：

1. 颜色字面量只能出现在 `theme/tokens.ts` 的 `RAW` 里（注释除外）；
2. `global.css` 的 `:root` 必须与 `tokens.ts` 的 `CSS_VAR_KEYS` 完全同步；
3. `WARNING_LEVEL` 必须覆盖 `client.ts` 里的 `WarningLevel` 全部等级；
4. 应用名在 `tokens.ts` 与 `index.html` 必须一致；favicon 用主色；
5. 全局样式必须走 `global.css`，不许再有内联 `<style>` 注入全局规则。

任何一条被破坏都会在这里失败，而不是等到界面上"看着有点不对劲"。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
TOKENS = SRC / "theme" / "tokens.ts"
ANTD_THEME = SRC / "theme" / "antd.ts"
GLOBAL_CSS = SRC / "styles" / "global.css"
MAIN_TSX = SRC / "main.tsx"
INDEX_HTML = ROOT / "frontend" / "index.html"
FAVICON = ROOT / "frontend" / "public" / "favicon.svg"
CLIENT_TS = SRC / "api" / "client.ts"

# 允许出现颜色字面量的文件（且仅限其 RAW 区块内）
COLOR_ALLOWED_FILE = TOKENS

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
FUNC_COLOR_RE = re.compile(r"\brgba?\s*\(")


# ────────────────────────── 工具 ──────────────────────────


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _slice(src: str, start: str, end: str) -> str:
    """取 `start` 与紧随其后的 `end` 之间的片段；找不到直接失败（避免静默跳过）。"""
    i = src.find(start)
    assert i >= 0, f"未找到起始标记：{start!r}（源码结构变了？请同步更新本测试）"
    j = src.find(end, i + len(start))
    assert j >= 0, f"未找到结束标记：{end!r}（源码结构变了？请同步更新本测试）"
    return src[i + len(start) : j]


def _strip_ts_comments(src: str) -> str:
    """剥掉 TS/TSX 注释，只留真正会生效的代码。

    注释里出现色值（例如「原来是某个红色，现改用令牌」）是合理的说明，
    不应被判为违规；但注释之外的色值一律不许有。
    `//` 前不是 `:` 才算行注释，避免误伤 `http://` 这类字符串。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", src)


def _kebab(key: str) -> str:
    """camelCase → kebab-case（键名换算规则，与 tokens.ts 的注释一致）。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", key).lower()


def _iter_frontend_sources():
    for p in sorted(SRC.rglob("*")):
        if p.suffix in (".ts", ".tsx"):
            yield p


def _raw_colors() -> dict[str, str]:
    """tokens.ts 的 RAW 色值表（键 → 大写色值）。"""
    block = _slice(_read(TOKENS), "const RAW = {", "} as const")
    return {k: v.upper() for k, v in re.findall(r"(\w+):\s*'(#[0-9A-Fa-f]{6})'", block)}


def _css_var_keys() -> list[str]:
    block = _slice(_read(TOKENS), "const CSS_VAR_KEYS = [", "] as const")
    return re.findall(r"'(\w+)'", block)


def _root_css_vars() -> dict[str, str]:
    block = _slice(_read(GLOBAL_CSS), ":root {", "\n}")
    return {k: v.upper() for k, v in re.findall(r"--wm-([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", block)}


# ────────────────────────── 1. 色值集中 ──────────────────────────


def test_no_color_literal_outside_tokens() -> None:
    """除 `theme/tokens.ts` 外，任何 ts/tsx 都不得出现颜色字面量（注释不算）。"""
    offenders: list[str] = []
    for p in _iter_frontend_sources():
        if p == COLOR_ALLOWED_FILE:
            continue
        code = _strip_ts_comments(_read(p))
        hits = HEX_RE.findall(code) + [m + "(" for m in FUNC_COLOR_RE.findall(code)]
        if hits:
            rel = p.relative_to(ROOT).as_posix()
            offenders.append(f"{rel}: {hits}")
    assert not offenders, (
        "以下文件绕过了设计令牌（请改用 theme/tokens.ts 里的令牌）：\n  "
        + "\n  ".join(offenders)
    )


def test_tokens_hex_only_inside_raw_block() -> None:
    """即使在 tokens.ts 内部，色值也只允许出现在 RAW 区块里。"""
    src = _read(TOKENS)
    raw = _slice(src, "const RAW = {", "} as const")
    outside = _strip_ts_comments(src.replace(raw, ""))
    hits = HEX_RE.findall(outside)
    assert not hits, f"tokens.ts 的 RAW 之外出现色值：{hits}；请把它们挪进 RAW。"


def test_global_css_hex_only_inside_root() -> None:
    """global.css 的色值只允许出现在 :root 里，规则体中一律用 var(--wm-*)。"""
    src = _read(GLOBAL_CSS)
    rest = re.sub(r":root\s*\{[^}]*\}", "", src, count=1, flags=re.S)
    rest = re.sub(r"/\*.*?\*/", "", rest, flags=re.S)
    hits = HEX_RE.findall(rest)
    assert not hits, f"global.css 的 :root 之外出现色值：{hits}；请改用 var(--wm-*) 并登记到 tokens.ts。"


# ────────────────────────── 2. CSS 变量与令牌同步 ──────────────────────────


def test_css_vars_cover_declared_keys() -> None:
    """CSS_VAR_KEYS 声明的键必须都在 :root 里，且取值与 RAW 一致。"""
    raw, keys, root = _raw_colors(), _css_var_keys(), _root_css_vars()
    missing, mismatch = [], []
    for key in keys:
        name = _kebab(key)
        if name not in root:
            missing.append(f"--wm-{name}")
        elif root[name] != raw[key]:
            mismatch.append(f"--wm-{name}: css={root[name]} vs tokens={raw[key]}")
    assert not missing, f"global.css 的 :root 缺少这些变量：{missing}"
    assert not mismatch, "global.css 与 tokens.ts 取值不一致：\n  " + "\n  ".join(mismatch)


def test_root_has_no_orphan_var() -> None:
    """:root 里不该有 CSS_VAR_KEYS 未登记的变量（否则成了无人管的第二份色值表）。"""
    expected = {_kebab(k) for k in _css_var_keys()}  # 与 _root_css_vars 一致，都不带 --wm- 前缀
    orphans = sorted(set(_root_css_vars()) - expected)
    assert not orphans, f":root 存在未登记的变量：{orphans}；请补进 tokens.ts 的 CSS_VAR_KEYS。"


def test_declared_keys_exist_in_raw() -> None:
    """CSS_VAR_KEYS 里不能有拼错的键名（拼错会导致换算出的变量悄悄对不上）。"""
    raw, keys = _raw_colors(), _css_var_keys()
    unknown = [k for k in keys if k not in raw]
    assert not unknown, f"CSS_VAR_KEYS 引用了 RAW 中不存在的键：{unknown}"


# ────────────────────────── 3. 预警等级覆盖 ──────────────────────────


def test_warning_level_covers_client_type() -> None:
    """tokens.ts 的 WARNING_LEVEL 必须覆盖 client.ts 的 WarningLevel 全部等级。

    后端新增等级时前端若忘了配色，这里会失败 —— 而不是等用户看到一列没颜色的数值。
    """
    declared = set(re.findall(r"'(\w+)'", _slice(_read(CLIENT_TS), "export type WarningLevel =", "\n")))
    assert declared, "未能从 client.ts 解析出 WarningLevel"
    block = _slice(
        _read(TOKENS), "WARNING_LEVEL: Record<WarningLevel, LevelStyle> = {", "\n}"
    )
    styled = set(re.findall(r"^\s{2}(\w+):\s*\{", block, flags=re.M))
    assert styled == declared, (
        f"WARNING_LEVEL 与 WarningLevel 不一致："
        f"缺少 {sorted(declared - styled)}，多出 {sorted(styled - declared)}"
    )


# ────────────────────────── 4. 应用名与 favicon ──────────────────────────


def test_app_name_matches_index_title() -> None:
    """`APP_NAME` 与 index.html 的 <title> 必须一致（避免两处各写一遍后走偏）。"""
    name = re.search(r"export const APP_NAME = '([^']+)'", _read(TOKENS))
    assert name, "未能从 tokens.ts 解析出 APP_NAME"
    title = re.search(r"<title>([^<]+)</title>", _read(INDEX_HTML))
    assert title, "index.html 缺少 <title>"
    assert title.group(1).strip() == name.group(1), (
        f"标题不一致：tokens.ts={name.group(1)!r} vs index.html={title.group(1)!r}"
    )


def test_favicon_exists_and_uses_brand_primary() -> None:
    """favicon 必须存在、被 index.html 引用，且底色取自主色令牌。"""
    assert FAVICON.is_file(), "缺少 frontend/public/favicon.svg"
    svg = _read(FAVICON)
    brand = _raw_colors()["brandPrimary"].upper()
    assert brand in svg.upper(), f"favicon.svg 未使用主色 {brand}"
    assert 'href="/favicon.svg"' in _read(INDEX_HTML), "index.html 未引用 favicon.svg"


# ────────────────────────── 5. 全局样式收口 ──────────────────────────


def test_global_css_is_imported_and_has_required_rules() -> None:
    """全局样式必须由 main.tsx 引入，且原先内联的三条规则仍在。"""
    assert "./styles/global.css" in _read(MAIN_TSX), "main.tsx 未引入 styles/global.css"
    css = _read(GLOBAL_CSS)
    for selector in (".ant-table-row-ignore td", ".warning-row-alarm td", ".warning-row-control td"):
        assert selector in css, f"global.css 缺少规则：{selector}"


def test_no_inline_global_style_tag() -> None:
    """不再用内联 <style> 注入全局规则（否则无法被工具检查、也会散落多处）。"""
    offenders = []
    for p in _iter_frontend_sources():
        code = _strip_ts_comments(_read(p))
        if "<style>" in code or "<style " in code:
            offenders.append(p.relative_to(ROOT).as_posix())
    assert not offenders, f"这些文件仍有内联 <style>：{offenders}；请移入 styles/global.css"


def test_antd_theme_wired() -> None:
    """antd 主题必须真正接上（否则按钮/选中态会退回 antd 默认蓝，与主色打架）。"""
    assert "theme={antdTheme}" in _read(MAIN_TSX), "main.tsx 未把 antdTheme 传给 ConfigProvider"
    theme = _read(ANTD_THEME)
    for key in ("colorPrimary", "colorError", "colorWarning", "colorSuccess"):
        assert key in theme, f"antd.ts 缺少 {key}"
    assert "from './tokens'" in theme, "antd.ts 必须从 tokens.ts 取色，不得另写色值"


# ────────────────────────── 6. 页面确实在用令牌 ──────────────────────────


@pytest.mark.parametrize(
    "rel",
    [
        "pages/ChartPage.tsx",
        "pages/ComparisonPage.tsx",
        "pages/ImportPage.tsx",
        "pages/PointsPage.tsx",
        "pages/ProjectsPage.tsx",
        "pages/RecordsPage.tsx",
        "App.tsx",
    ],
)
def test_pages_consume_tokens(rel: str) -> None:
    """这些文件必须从 theme/tokens 取色（防止以后有人就地写死颜色）。"""
    src = _read(SRC / rel)
    assert "theme/tokens" in src, f"{rel} 未引用设计令牌"


def test_comparison_page_has_single_level_color_helper() -> None:
    """ComparisonPage 的两列曾各写一遍等级判断，现已合并为 levelValueCell。"""
    src = _read(SRC / "pages" / "ComparisonPage.tsx")
    assert "levelValueCell" in src, "未找到合并后的等级着色函数"
    assert src.count("render: levelValueCell") == 2, "ΔV / C 两列应复用同一个渲染函数"


def test_chart_page_uses_shared_palette() -> None:
    """曲线图的调色板必须来自令牌（原先 ChartPage 自带一套与其并行的 8 色）。"""
    src = _read(SRC / "pages" / "ChartPage.tsx")
    assert "SERIES_PALETTE" in src, "ChartPage 未使用 SERIES_PALETTE"
    # 一处给 series 的 itemStyle，一处给 option.color
    assert src.count("SERIES_PALETTE") >= 3, "SERIES_PALETTE 未同时用于 itemStyle 与 option.color"
    assert "const PALETTE" not in src, "ChartPage 又定义了本地调色板，请改用令牌"
