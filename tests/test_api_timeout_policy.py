# -*- coding: utf-8 -*-
"""守卫：慢接口必须有自己的超时，超时提示必须是人话。

【2026-10-09 真实事故】用户导出 24 期日报，界面报 `timeout of 15000ms exceeded`。

查下来是**前端把活掐了，后端其实每次都干完了**：
- `logs/backend.log` 里同一批批量导出被记了 5 次，**5 次全是 200 OK**；
- 实测（副本库 + 8011 端口，`.verify_tmp/perf/`）：后端 8.9 秒、产物 28 MB；
- 而 `client.ts` 里全局 `timeout: 15000` —— 生成 + 传输 + 代理叠起来刚好压线。

两件事都错：
1. **一个数包打天下**。快接口（列表/详情）该 15 秒失败，慢接口（导出/生成/导入/备份）
   天然要几十秒，用同一个数就是"慢活必然被误杀"；
2. **错误提示直接把 axios 的英文原文抛给用户**，现场看不懂，也不知道下一步怎么办。

于是本文件守：
- `FAST_TIMEOUT` / `LONG_TIMEOUT` 两档存在，且**慢接口逐个显式带 `LONG_TIMEOUT`**；
- 不许再出现 `timeout: 15000` 这种裸字面量（要改就改常量）；
- 超时提示是中文，且带上"已等待多久 / 怎么办"；
- 批量导出既给了 `LONG_TIMEOUT` 也给了 `timeoutHint`（它能一口气导几十期，最容易踩）；
- 导出页对重复点击有显式拦截（那次 5 次请求就是这么来的）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"
REPORTS_PAGE = ROOT / "frontend" / "src" / "pages" / "ReportsPage.tsx"

#: 天生就慢的接口 —— 生成、导入、导出、备份、整卷删除。
#: 新增这类接口时**必须**把它加进来，否则会被 15 秒的通用超时误杀。
#: 注：这里只收**名字唯一**的方法。`create` 这种在 4 个 API 组里各有一份的短名不能放进来
#: （项目/测点/记录的 create 都是毫秒级的快接口，混在一起没法判定）。
SLOW_METHODS = (
    "createSample",
    "hardDelete",
    "upload",
    "recheck",
    "confirm",
    "preview",
    "commit",
    "generate",
    "cleanup",
    "recompute",
    "fixValues",
    "project",
    "downloadExcel",
    "downloadExcelBatch",
    "downloadDaily",
    "uploadTemplate",
    "restore",
)

#: 一个对象属性的起点：恰好 2 个空格缩进 + 标识符 + `: (`
_PROP_START = re.compile(r"^\s{2}(\w+)\s*:\s*\(")


def _client_text() -> str:
    assert CLIENT.exists(), f"找不到 {CLIENT}"
    return CLIENT.read_text(encoding="utf-8")


def _segments(text: str) -> dict[str, list[str]]:
    """把 client.ts 按**顶层的 API 方法**切开：{方法名: [每处的方法体]}。

    只认"2 个空格缩进 + `名字: (`"开头的行 —— 对象方法都是这个缩进，
    方法内部（≥4 空格）和 interface 字段（`  id: number`，没有 `(`）都不会被误切。
    同名方法出现多处（`hardDelete` 在项目和测点里各有一份）时全都收进来。

    ⚠ 两种收尾写法都要认：`名字: (x) => {` 用 `  },` 收尾，
    单表达式的 `名字: (x) => http.get(...)` 没有花括号 —— 只能靠**下一个属性开头**收尾。
    （先按"遇到下一处属性就覆盖"写过一版，单表达式方法全丢了，测试当场就红了。）
    """
    out: dict[str, list[str]] = {}
    cur_name: str | None = None
    cur_body: list[str] = []

    def flush() -> None:
        if cur_name is not None:
            out.setdefault(cur_name, []).append("\n".join(cur_body))

    for line in text.splitlines():
        m = _PROP_START.match(line)
        if m:
            flush()
            cur_name, cur_body = m.group(1), [line]
            continue
        if cur_name is not None:
            cur_body.append(line)
            if line == "}" or re.match(r"^\s{2}\},\s*$", line):
                flush()
                cur_name, cur_body = None, []
    flush()
    return out


def _function_body(text: str, name: str) -> str:
    """取一个**顶层函数**的函数体（从 `function name` 那行到列 0 的 `}` 为止）。

    不能简单地 split("\\n}") —— 参数类型本身就会在列 0 出现 `}): string | null {`。
    """
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if f"function {name}" in l)
    for i in range(start + 1, len(lines)):
        if lines[i] == "}":
            return "\n".join(lines[start : i + 1])
    raise AssertionError(f"找不到函数 {name} 的结尾")


def test_slow_endpoints_carry_the_long_timeout() -> None:
    """慢接口必须显式带 `LONG_TIMEOUT` —— 缺一个就是"必然会超时"的坑。"""
    segs = _segments(_client_text())
    missing: list[str] = []
    for name in SLOW_METHODS:
        bodies = segs.get(name)
        if not bodies:
            missing.append(f"{name}: 在 client.ts 里找不到这个方法（改名了就把守卫一起改）")
            continue
        for body in bodies:
            if "LONG_TIMEOUT" not in body:
                first = body.splitlines()[0].strip()[:90]
                missing.append(f"{name}: 没带 LONG_TIMEOUT  ——  {first}")
    assert not missing, (
        "这些接口天生就慢（生成/导入/导出/备份），必须显式给 LONG_TIMEOUT，"
        "否则会被 15 秒的通用超时掐断（后端白算）：\n" + "\n".join(missing)
    )


def test_no_bare_timeout_literals() -> None:
    """不许写裸超时字面量 —— 只能引用 FAST_TIMEOUT / LONG_TIMEOUT 两个常量。"""
    text = _client_text()
    bad = [f"{i}: {l.strip()}" for i, l in enumerate(text.splitlines(), 1)
           if re.search(r"timeout\s*:\s*\d", l)]
    assert not bad, "超时值必须走常量（FAST_TIMEOUT / LONG_TIMEOUT），不要写字面量：\n" + "\n".join(bad)
    assert "export const FAST_TIMEOUT" in text, "FAST_TIMEOUT 不见了"
    assert "export const LONG_TIMEOUT" in text, "LONG_TIMEOUT 不见了"
    # 通用默认必须是快档：慢活靠各接口自己声明，不能被整体调大掩盖掉
    assert "timeout: FAST_TIMEOUT," in text, "axios 实例的默认超时必须是 FAST_TIMEOUT"


def test_timeout_is_translated_into_a_human_sentence() -> None:
    """超时必须翻译成人话 —— 不能把 `timeout of 15000ms exceeded` 抛给现场。"""
    text = _client_text()
    assert "function timeoutMessage" in text, "timeoutMessage() 不见了（超时提示的唯一出处）"
    body = _function_body(text, "timeoutMessage")
    assert "已中断" in body, "超时提示要说明'这次已中断'"
    assert "秒" in body, "超时提示要带上'等了多久'"
    assert "ECONNABORTED" in body, "要按 axios 的超时码判断（不能只靠英文 message 里有没有 timeout）"


def test_batch_export_gets_a_hint_that_says_what_to_do() -> None:
    """批量导出是最容易超时的那个（能一口气导几十期），必须给可执行的话。"""
    segs = _segments(_client_text())
    body = "\n".join(segs.get("downloadExcelBatch", []))
    assert "LONG_TIMEOUT" in body, "批量导出没带 LONG_TIMEOUT"
    assert "timeoutHint" in body, "批量导出没有超时提示（用户不知道该缩小日期范围）"


def test_reports_page_blocks_double_click() -> None:
    """导出页要显式挡重复点击 —— 2026-10-09 那批导出被点了 5 次、后端白算 5 遍。"""
    src = REPORTS_PAGE.read_text(encoding="utf-8")
    assert "if (loadingExcel) return" in src, "导出函数开头缺防连点"
    assert "message.loading" in src, "批量导出要有'正在生成…'的常驻提示，否则用户以为点坏了"
