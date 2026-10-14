# -*- coding: utf-8 -*-
"""回归测试：日期选择面板（中文化 + 节假日标记 + 打点实时刷新）。

节假日**数据**的测试在 `tests/test_holidays.py`（数据在服务端）。
这里只管前端的三件事：

1. **月份/星期必须是中文**：`ConfigProvider locale={zhCN}` 只管「请选择日期/确定」这类文案，
   面板里的月份名与星期缩写取自 dayjs 自己的语言包。少了 `import 'dayjs/locale/zh-cn'`
   就回退英文（实测显示「2026年Sep」+ Su Mo Tu），而这**不报任何错**。
2. **标记的层次与优先级**：调休上班日优先于周末；预警底色优先于节假日底色。
3. **打点实时刷新**：写完观测数据后日历要跟着变，且有兜底。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
HOLIDAYS = SRC / "calendar" / "holidays.ts"
CELL = SRC / "components" / "CalendarCell.tsx"
PROVIDER = SRC / "components" / "HolidayProvider.tsx"
MAIN = SRC / "main.tsx"
RECORDS = SRC / "pages" / "RecordsPage.tsx"
IMPORT = SRC / "pages" / "ImportPage.tsx"
BATCH = SRC / "pages" / "BatchProjectsPage.tsx"
REPORTS = SRC / "pages" / "ReportsPage.tsx"
CLIENT = SRC / "api" / "client.ts"


def _read(p: Path) -> str:
    assert p.is_file(), f"缺少文件：{p}"
    return p.read_text(encoding="utf-8")


def _slice(src: str, start: str, end: str) -> str:
    i = src.find(start)
    assert i >= 0, f"未找到起始标记：{start!r}（源码结构变了？请同步更新本测试）"
    j = src.find(end, i + len(start))
    assert j >= 0, f"未找到结束标记：{end!r}（源码结构变了？请同步更新本测试）"
    return src[i + len(start) : j]


# ────────────────────────── 1. 日期面板中文化 ──────────────────────────


def test_dayjs_chinese_locale_loaded() -> None:
    """必须显式加载 dayjs 中文包并设为默认。

    这条是"月份变回 Sep、星期变回 Su Mo Tu"的唯一防线 ——
    ConfigProvider 的 locale 不覆盖月份名与星期缩写，漏了不会报错也不会失败。
    """
    src = _read(MAIN)
    assert "dayjs/locale/zh-cn" in src, "main.tsx 未引入 dayjs 的中文语言包，日期面板月份会变英文"
    assert re.search(r"dayjs\.locale\(\s*'zh-cn'\s*\)", src), "main.tsx 未把 dayjs 的默认语言设为 zh-cn"


# ────────────────────────── 2. 判断逻辑 ──────────────────────────


def test_mark_of_checks_makeup_before_weekend() -> None:
    """`markOf` 里必须先判调休上班、再判周末。

    顺序反了的话，2026-02-14（周六补班）会被标成"周末休息"，与官方安排完全相反 ——
    而界面上只是少个「班」字，看不出来。
    """
    body = _slice(_read(HOLIDAYS), "export function markOf(", "\n}")
    i_makeup = body.find("table.makeup[key]")
    i_weekend = body.find("date.day()")
    assert i_makeup >= 0 and i_weekend >= 0, "markOf 结构变了，请同步更新本测试"
    assert i_makeup < i_weekend, "markOf 必须先判调休上班日、再判周末（否则 02-14 会被标成休息）"


def test_mark_of_tolerates_missing_table() -> None:
    """拿不到节假日数据时必须降级成"只判周末"，不能抛异常。"""
    src = _read(HOLIDAYS)
    body = _slice(src, "export function markOf(", "\n}")
    assert "if (table)" in body, "markOf 未对 table 做空值判断：接口拿不到数据时会整片日历报错"


# ────────────────────────── 3. 节假日数据来自服务端 ──────────────────────────


def test_frontend_has_no_builtin_holiday_dates() -> None:
    """前端不得再自带具体日期。

    两端各存一份 2026 数据，早晚会出现"服务端联网拿到 2027 了、前端还写着 2026"。
    数据只在服务端（联网 → 缓存 → 内置）。
    """
    src = _read(HOLIDAYS)
    hits = re.findall(r"'20\d\d-\d\d-\d\d'", src)
    assert not hits, f"前端又内置了具体节假日日期：{hits}；数据应由服务端提供"


def test_holiday_provider_wired_in_main() -> None:
    """Provider 必须真的挂在树上（否则所有格子都拿不到数据，日历静默失去标记）。"""
    main = _read(MAIN)
    assert "HolidayProvider" in main and "<HolidayProvider>" in main, "main.tsx 未挂载 HolidayProvider"
    assert "</HolidayProvider>" in main, "HolidayProvider 未闭合"


def test_provider_dedupes_requests_per_year() -> None:
    """同一年只能请求一次 —— 一个面板 42 个格子，不能打 42 次接口。"""
    src = _read(PROVIDER)
    assert "requested" in src and "has(year)" in src, "HolidayProvider 缺少按年去重，会重复请求"
    assert "<HolidayContext.Provider" in src, "HolidayProvider 未提供 context"


def test_picker_fetches_year_on_cell_render() -> None:
    """格子渲染时要触发该年份的加载（翻到 2030 年也得能拿到那一年的数据）。"""
    src = _read(CELL)
    assert "useHolidayTable" in src and "useHolidayEnsure" in src, "CalendarCell 未接入节假日数据"
    assert "ensure(year)" in src, "CalendarCell 未在渲染时触发年份加载"


def test_api_client_has_holiday_endpoints() -> None:
    src = _read(CLIENT)
    assert "holidayApi" in src, "client.ts 缺少 holidayApi"
    assert "/holidays/${year}" in src, "缺少按年取节假日的接口"


# ────────────────────────── 4. 标记接到了各页面的日期选择器 ──────────────────────────


@pytest.mark.parametrize(
    "path,label",
    [
        (RECORDS, "观测记录页"),
        (IMPORT, "Excel 导入页"),
        (BATCH, "批量建项目页"),
        (REPORTS, "成果导出页"),
    ],
)
def test_pickers_have_holiday_cells(path: Path, label: str) -> None:
    """各处日期选择器都要挂上节假日标记（改了一处漏一处的经典场景）。

    三种合规写法：
    · 展开共享配置 `{...plainCalendarPanel}`（只关心节假日时）；
    · 展开 `{...observationCalendarPanel(dates)}`（要在节假日之上再标记观测日期，
      导出页用这个 —— 它内部同样走 CalendarCell + CalendarLegend，节假日不会丢）；
    · 直接用 `CalendarCell` + `CalendarLegend`（页面自己完全自定义时，如记录页）。
    """
    src = _read(path)
    ok = (
        "plainCalendarPanel" in src
        or "observationCalendarPanel" in src
        or ("CalendarCell" in src and "CalendarLegend" in src)
    )
    assert ok, f"{label}的日期选择器未挂节假日标记（CalendarCell / plainCalendarPanel / observationCalendarPanel）"


def test_plain_panel_covers_legend_and_cell() -> None:
    """共享配置必须同时提供格子与图例（否则会出现"有色块没说明"）。"""
    src = _read(CELL)
    block = _slice(src, "export const plainCalendarPanel = {", "\n}")
    assert "CalendarCell" in block and "CalendarLegend" in block, "plainCalendarPanel 未同时提供格子与图例"


def test_observation_panel_covers_cell_legend_and_mark() -> None:
    """带观测标记的面板同样必须同时提供格子与图例，且标记要挂在横条上。

    导出页的日期标记就是靠这个配置（有数据带横条、没有就不带），
    写成"格子有了、图例没写"用户只会看到一条不明所以的色块。
    """
    src = _read(CELL)
    block = _slice(src, "export function observationCalendarPanel(", "\n}\n")
    assert "CalendarCell" in block and "CalendarLegend" in block, "未同时提供格子与图例"
    assert "barColor" in block, "观测标记没有挂到横条上"


def test_calendar_cell_prioritizes_warning_bg() -> None:
    """底色优先级：传入的预警底色要盖过节假日底色（安全信息 > 放假信息）。

    实现方式是 `bg ?? holidayBg`；写成 `holidayBg ?? bg` 会让放假把报警红顶掉。
    """
    src = _read(CELL)
    assert "bg ?? holidayBg" in src, "CalendarCell 的底色优先级被改动：应传 bg（预警）优先于节假日底色"


def test_calendar_cell_uses_tokens_only() -> None:
    """新组件的颜色必须来自令牌（这条与 test_frontend_theme 互补，就近提示）。"""
    src = _read(CELL)
    assert "theme/tokens" in src, "CalendarCell 未引用设计令牌"


def test_legend_width_capped() -> None:
    """图例必须限宽。

    不给上限的话，图例文字会把整个下拉撑宽 —— 实测撑到 612px，而日历本体只有 288px，
    于是日历右边凭空多出一大片空白（用户会问"这空白是干嘛的"）。
    """
    src = _read(CELL)
    assert "PANEL_CONTENT_WIDTH" in src, "缺少面板内容宽度常量"
    assert "maxWidth: PANEL_CONTENT_WIDTH" in src, "图例未限宽，会把日期下拉撑宽"


# ────────────────────────── 5. 打点实时刷新 ──────────────────────────


def test_records_picker_refreshes_marks_on_open() -> None:
    """记录页的日期面板**打开时**要重新拉一次打点数据。

    这是兜底：任何"写完数据忘了刷新"的路径，用户一打开面板就会被纠正，
    不必指望每个写操作都记得调 loadBaselines()。
    """
    src = _read(RECORDS)
    assert "onOpenChange" in src, "日期选择器缺少 onOpenChange 兜底刷新"
    block = _slice(src, "onOpenChange={(open) => {", "}}")
    assert "loadBaselines()" in block, "打开面板时没有刷新日期打点"


def test_generate_and_cleanup_refresh_marks() -> None:
    """生成/清理演示数据后必须刷新打点 —— 原先正是这里漏了，导致日期表不显示新日期。"""
    src = _read(RECORDS)
    for marker, end in (("已生成", "setGenLoading(false)"), ("已清理", "setGenResult(null)")):
        block = _slice(src, marker, end)
        assert "loadBaselines()" in block, f"{marker}那条路径后没有刷新日期打点"


def test_no_unconditional_today_mark() -> None:
    """已录入标记不能写成"当前选中日期恒为真"。

    原写法 `recordDates.includes(key) || date.format(...) === key` 恒真，
    于是没数据的日期也带一条绿杠 —— 把"没录上"伪装成了"已录入"。
    """
    src = _read(RECORDS)
    assert "|| date.format('YYYY-MM-DD') === key" not in src, (
        "已录入标记又回到「当前日期恒为真」的写法：没数据的日期也会显示绿条"
    )
