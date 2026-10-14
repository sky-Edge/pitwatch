# -*- coding: utf-8 -*-
"""扫描 backend 里"只在函数体内导入"的第三方包。

用途：打包时决定要显式声明哪些 `--hidden-import`。

模块级导入会被 PyInstaller 自动收集；**只在函数体内导入**的包，静态分析虽然通常也能
发现，但属于需要重点确认的一类 —— 漏了的后果是：服务能正常启动、页面能正常用，
直到用户走到那条代码路径才炸 `ModuleNotFoundError`（例如上传 .xls 台账时才用到 xlrd）。

命令行：`python build_tools/scan_lazy_imports.py`
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Python 标准库（不全，但覆盖本项目实际会用到的那部分）
STDLIB = {
    "__future__", "abc", "argparse", "ast", "asyncio", "atexit", "base64", "binascii",
    "calendar", "codecs", "collections", "concurrent", "contextlib", "copy", "csv", "ctypes",
    "dataclasses", "datetime", "decimal", "difflib", "email", "enum", "errno", "fractions",
    "functools", "getpass", "glob", "gzip", "hashlib", "hmac", "html", "http", "importlib",
    "inspect", "io", "itertools", "json", "locale", "logging", "math", "mimetypes",
    "multiprocessing", "ntpath", "numbers", "operator", "os", "pathlib", "pickle", "pkgutil",
    "platform", "posixpath", "queue", "random", "re", "secrets", "shutil", "signal", "socket",
    "sqlite3", "ssl", "stat", "statistics", "string", "struct", "subprocess", "sys", "tempfile",
    "textwrap", "threading", "time", "traceback", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "warnings", "wave", "weakref", "webbrowser", "zipfile", "zlib",
}

_OWN = "backend"          # 本项目自己的包，不需要声明
LOCAL_SKIP = {"backend", ""}


def _top_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {a.name.split(".")[0] for a in node.names}
    if isinstance(node, ast.ImportFrom):
        return {(node.module or "").split(".")[0]}
    return set()


def find_lazy_only_modules(root: Path | None = None) -> set[str]:
    """返回"只出现在函数体内、别处没有模块级导入"的第三方顶层模块名。"""
    root = root or ROOT / "backend"
    module_level: set[str] = set()
    lazy: set[str] = set()

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        top_nodes = set(id(n) for n in getattr(tree, "body", []))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = {n for n in _top_names(node) if n not in STDLIB and n not in LOCAL_SKIP}
            if not names:
                continue
            if id(node) in top_nodes:
                module_level |= names
            else:
                lazy |= names
    return lazy - module_level


def main() -> int:
    lazy_only = find_lazy_only_modules()
    print("只在函数体内导入的第三方包（需要显式 --hidden-import）：")
    if lazy_only:
        for mod in sorted(lazy_only):
            print(f"  - {mod}")
    else:
        print("  （无）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
