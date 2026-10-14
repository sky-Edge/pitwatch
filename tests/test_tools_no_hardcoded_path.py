# -*- coding: utf-8 -*-
"""回归测试：tools/ 脚本不得写死本机绝对路径。

背景：本项目已因"写死资源路径"栽过两次（node 版本目录、python venv 路径），
tools/ 里的三个一次性脚本又把 Excel 台账目录写死在 D:\\文件\\... 下。
本测试锁住「目录参数化 + 环境变量可覆盖」这一行为，防止以后回退。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

PY_SCRIPTS = ("inspect_obs_excel.py", "inspect_obs_data.py", "bulk_import.py")

# docstring / 注释里举例用的路径，允许出现
_ALLOWED_PATH_CONTEXT = ("MONITOR_LEDGER_DIR", "MONITOR_API_BASE", "127.0.0.1", "某目录")


def _source(name: str) -> str:
    return (TOOLS / name).read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """剥掉 docstring 与 # 注释，只留真正会执行的代码。

    用 ast 解析后按行取源码片段，比正则更可靠。
    """
    tree = ast.parse(src)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_lines.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc_lines:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


@pytest.mark.parametrize("name", PY_SCRIPTS)
def test_no_hardcoded_absolute_path_in_code(name: str) -> None:
    """代码里不得出现写死的 Windows 绝对路径（注释与 docstring 不算）。"""
    code = _code_only(_source(name))
    hits = []
    for i, line in enumerate(code.splitlines(), 1):
        for m in re.finditer(r'[A-Za-z]:\\\\[^"\'\n]{3,90}', line):
            if any(a in line for a in _ALLOWED_PATH_CONTEXT):
                continue
            hits.append((i, m.group(0)))
    assert not hits, f"{name} 仍写死绝对路径：{hits}"


@pytest.mark.parametrize("name", PY_SCRIPTS)
def test_ledger_dir_env_override(name: str) -> None:
    """台账目录必须支持环境变量覆盖（MONITOR_LEDGER_DIR 或 --dir）。"""
    src = _source(name)
    has_env = "MONITOR_LEDGER_DIR" in src
    has_arg = "--dir" in src
    assert has_env or has_arg, f"{name} 既不支持环境变量也不支持 --dir，目录仍被写死"


@pytest.mark.parametrize("name", PY_SCRIPTS)
def test_help_flag_works(name: str) -> None:
    """带 argparse 的脚本 --help 必须正常退出（保证参数解析没写坏）。"""
    r = subprocess.run(
        [sys.executable, str(TOOLS / name), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"{name} --help 失败：{r.stderr[:200]}"
    assert "usage" in (r.stdout or "").lower()


def test_missing_dir_gives_friendly_error() -> None:
    """目录不存在时应打印人话并退出码 2，而不是抛 traceback。"""
    r = subprocess.run(
        [sys.executable, str(TOOLS / "inspect_obs_excel.py"), "--dir", r"Z:\__nope__"],
        capture_output=True, text=True, timeout=60,
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 2
    assert "台账目录不存在" in out
    assert "Traceback" not in out
