# -*- coding: utf-8 -*-
"""守卫：源码不能带"被编辑事故"留下的伤。

【2026-10-09 真实事故】`frontend/src/components/WarningRulesModal.tsx` 里两行被挤成一行：

    } from 'antd'import type { ColumnsType } from 'antd/es/table'
                   ^ 这里少了换行

- **没有语法检查时完全看不出来**：Python 测试全绿、后端照常跑；
- 前端只有**真正加载到这个模块**时才由 Vite 报 `Pre-transform error: Missing semicolon`，
  也就是说用户点开「预警规则」才会发现打不开（实测就是这么暴露的）；
- 同类事故还有"写回时把两处替换串了"等，判据都是**文件里出现了不该连着出现的两段**。

本文件守两条最便宜、最精确的判据：
1. `from '…'` / `from "…"` 的闭合引号后**紧跟**一个语句关键字（中间什么都没有）
   → 两行被挤成一行；
2. 源码里出现 U+FFFD 替换字符 `�` → 某次按错误编码读写把字节丢了（本项目的编码坑见
   `test_console_encoding.py`）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 扫描范围：会被人手工改的源码（第三方/构建产物不算）
_SCAN_DIRS = ("frontend/src", "backend", "tools", "build_tools")
_SCAN_SUFFIX = {".ts", ".tsx", ".py", ".css", ".html"}
_SKIP_PARTS = {"__pycache__", "node_modules", ".pytest_cache", "release", "build_pyi", "dist"}

#: `from '相对路径'` 的闭合引号后面直接粘上 `import/const/...` —— 典型的"两行挤一行"
_MERGED_STATEMENT = re.compile(
    r"""from\s+['"][^'"]+['"]\s*(import|export|const|let|var|function|class|return)\b"""
)


def _source_files() -> list[Path]:
    out: list[Path] = [ROOT / "launcher.py"]
    for sub in _SCAN_DIRS:
        base = ROOT / sub
        if not base.is_dir():
            continue
        out += [
            p for p in base.rglob("*")
            if p.suffix in _SCAN_SUFFIX and not (_SKIP_PARTS & set(p.parts))
        ]
    return out


def test_no_merged_statements() -> None:
    """不许出现"两行被挤成一行"（`from 'x'import y` 这种）。"""
    bad: list[str] = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:            # 由下面那条测试更精确地报
            bad.append(f"{path.relative_to(ROOT)} 不是 UTF-8：{e}")
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _MERGED_STATEMENT.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{i}  {line.strip()[:100]}")
    assert not bad, (
        "这些行像是「两行挤成一行」（多半是写回时丢了个换行），"
        "前端要等真正加载到那个模块才会报错：\n" + "\n".join(bad)
    )


def test_no_replacement_characters_in_source() -> None:
    """源码里不许出现 U+FFFD（`�`）——那是按错误编码读写的残留，字符已经被吃掉了。"""
    bad: list[str] = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "\ufffd" in line:
                bad.append(f"{path.relative_to(ROOT)}:{i}  {line.strip()[:100]}")
    assert not bad, "源码里出现替换字符，说明某次读写用了不匹配的编码：\n" + "\n".join(bad)
