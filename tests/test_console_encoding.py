# -*- coding: utf-8 -*-
"""守卫：启动路径上的输出**不能因为"打不出来"而把后端带走**。

【2026-10-09 真实事故】用户点「启动系统.bat」→ 黑窗口一片乱码 → 后端根本没起来。
日志里是：

    File "backend\\core\\db.py", line 113, in migrate_template_library
        print(f"...回填 {backfilled} 条 模板↔项目 关联")
    UnicodeEncodeError: 'gbk' codec can't encode character '\\u2194'
    ERROR:    Application startup failed. Exiting.

控制台是 **GBK**，`↔`(U+2194) 与 `⚠`(U+26A0) 编不出来 → `print` 抛异常 →
`init_db()` 失败 → 整个后端起不来。而**测试全绿** —— 因为 pytest 把 stdout 换成了
自己的缓冲（不是 GBK），这条坑在单元测试里根本露不出来。

三道保险，本文件分别守：
1. `harden_console()` 把 stdout/stderr 的 errors 改成 replace（只改 errors，不动编码，
   日志文件仍是原来的 GBK）；
2. `_safe_print()` 自身永不抛（连降级都失败就当没这行日志）；
3. **源码里所有 `print` 的字面量必须是 GBK 可编码的** —— 不靠"运行时兜住"，
   而是根本不出这种字符（GBK 里没有的符号一律别往控制台写）。

补充事故（同一天下午发现，同一类问题的另一半）：
`logs/backend.log` 里 **ASCII 段正常、中文段全是 GBK 字节**，文件用 UTF-8 打不开。
原因是子进程 stdout 被重定向到**文件**后，Python 不再看控制台代码页，而用区域默认编码（GBK），
而 launcher 把日志文件按 UTF-8 打开 —— 两边不一致。→ launcher 显式给子进程
`PYTHONIOENCODING=utf-8`，本文件最后两条守这条一致性。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 这些符号在 GBK 里**不存在**（实测）：↔ ⚠ ✓ ✗ … 而 · — ≥ ≈ × ① √ △ 是有的
_KNOWN_NOT_IN_GBK = "↔⚠✓✗"


def _files_to_scan() -> list[Path]:
    out: list[Path] = [ROOT / "launcher.py"]
    for sub in ("backend", "build_tools", "tools"):
        out.extend(p for p in (ROOT / sub).rglob("*.py") if "__pycache__" not in p.parts)
    return out


def _printed_literals(node: ast.AST):
    """取出 print/_safe_print 里**会真的打出去**的字符串字面量（含 f-string 的固定部分）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                yield v.value
    elif isinstance(node, ast.BinOp):
        yield from _printed_literals(node.left)
        yield from _printed_literals(node.right)
    elif isinstance(node, (ast.Call, ast.BoolOp, ast.IfExp, ast.Tuple, ast.List)):
        for child in ast.iter_child_nodes(node):
            yield from _printed_literals(child)


def test_all_printed_literals_are_gbk_encodable() -> None:
    """控制台是 GBK：打印的字符串里不许出现 GBK 编不出的字符。

    这条是"源头守卫"——即便将来 `harden_console()` 被谁挪走或失效，
    也不会有 `↔` `⚠` 这种字符躺在 print 里等着一枪打穿启动流程。
    """
    bad: list[str] = []
    for path in _files_to_scan():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:            # 语法都不对的话由别的测试去报
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in {"print", "_safe_print"}:
                continue
            for text in _printed_literals(node):
                try:
                    text.encode("gbk")
                except UnicodeEncodeError as e:
                    bad.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} 里的 {text[:40]!r} —— "
                        f"{e.object[e.start:e.end]!r} 不是 GBK 字符"
                    )
    assert not bad, "这些 print 会在 GBK 控制台上抛异常、有可能直接让后端起不来：\n" + "\n".join(bad)


def test_safe_print_never_raises_on_gbk(monkeypatch) -> None:
    """`_safe_print` 必须自己兜住 —— 不管底层流怎么炸都不能往外抛。"""
    from backend.core.db import _safe_print

    class _GbkOnly:
        encoding = "gbk"

        def write(self, s: str) -> None:      # noqa: D401
            s.encode("gbk")                    # 编不出就抛 UnicodeEncodeError

        def flush(self) -> None:
            pass

    monkeypatch.setattr(sys, "stdout", _GbkOnly())
    _safe_print(f"这里有个 {_KNOWN_NOT_IN_GBK[0]} 和 {_KNOWN_NOT_IN_GBK[1]}")   # 不该抛


def test_harden_console_is_called_before_init_db() -> None:
    """`harden_console()` 必须在 `init_db()` **之前**执行（模块导入期，而不是 lifespan 里）。

    判据用"顶格的调用语句"（`\\nharden_console()\\n`）—— 只看字符串出现的先后会被
    注释里提到的 `init_db()` 骗到（第一版守卫就是这么挂的）。
    """
    src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    assert "\nharden_console()\n" in src, "main.py 没在模块导入期调用 harden_console()"
    assert src.index("\nharden_console()\n") < src.index("async def lifespan"), (
        "harden_console() 必须排在 lifespan 之前 —— 迁移里的 print 就发生在 init_db() 内"
    )
    # 启动器自己有 print，也要兜：编码钉 UTF-8 + 编不出的降级成 ?，两者缺一不可
    # （口径与打包入口 build_tools/app_entry.py 一致）
    launcher_src = (ROOT / "launcher.py").read_text(encoding="utf-8")
    assert re.search(
        r'reconfigure\(\s*encoding="utf-8"\s*,\s*errors="replace"\s*\)', launcher_src
    ), "launcher.py 的 reconfigure 必须同时钉 encoding=utf-8 与 errors=replace"


def test_migration_print_survives_a_gbk_console(tmp_path) -> None:
    """端到端：把子进程的 stdout 设成 GBK，跑一遍"会打印非 GBK 字符"的路径。

    这是最接近真机的一次验证 —— 修之前这里必炸（UnicodeEncodeError → 退出码非 0）。
    """
    code = (
        "import sys;"
        f"sys.path.insert(0, r'{ROOT}');"
        "from backend.core.db import harden_console, _safe_print;"
        "harden_console();"
        f"print('迁移：模板{_KNOWN_NOT_IN_GBK[0]}项目 {_KNOWN_NOT_IN_GBK[1]} 完成');"
        f"_safe_print('再来一次 {_KNOWN_NOT_IN_GBK[0]}');"
        "print('DONE')"
    )
    env = {
        **os.environ,
        "PYTHONIOENCODING": "gbk",                       # 强制 GBK 控制台
        "MONITOR_DB_URL": f"sqlite:///{(tmp_path / 't.db').as_posix()}",
    }
    p = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, env=env, timeout=60
    )
    assert p.returncode == 0, (
        "GBK 控制台下打印非 GBK 字符把进程打挂了：\n"
        + p.stderr.decode("gbk", errors="replace")[-800:]
    )
    assert "DONE" in p.stdout.decode("gbk", errors="replace")


# ---------------------------------------------------------------------------
# 图标常量（把符号先赋给变量、再打印的那种写法）
# ---------------------------------------------------------------------------

def test_symbolic_literals_are_gbk_encodable() -> None:
    """「长度≤3 且不含字母/数字/汉字」的字符串字面量 = 图标常量，必须 GBK 可编码。

    为什么单独立一条：`_printed_literals` 只能看见**写在 print 括号里**的字面量。
    而现实写法是

        flag = "✓" if n_error == 0 else "△"      # ← ✗ 在这里，print 里只有一个变量名
        print(f"{flag} {name}: ...")

    2026-10-09 `tools/bulk_import.py` 就是这么写的：print 扫描一条都没报，
    但真跑到"出错那一支"就会在 GBK 控制台上抛 UnicodeEncodeError。
    """
    def _is_icon(s: str) -> bool:
        return 1 <= len(s) <= 3 and not any(
            c.isalnum() or "\u4e00" <= c <= "\u9fff" for c in s
        )

    bad: list[str] = []
    for path in _files_to_scan():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            texts: list[str] = []
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                texts.append(node.value)
            elif isinstance(node, ast.JoinedStr):
                texts += [
                    v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ]
            for text in texts:
                if not _is_icon(text):
                    continue
                try:
                    text.encode("gbk")
                except UnicodeEncodeError:
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno}  {text!r}")
    assert not bad, (
        "这些图标字符 GBK 里没有，一旦被打到控制台/日志就是一颗雷：\n" + "\n".join(bad)
    )


# ---------------------------------------------------------------------------
# 日志文件编码：子进程写的一方，和 launcher 开文件的一方，必须是同一种
# ---------------------------------------------------------------------------

def _load_launcher():
    spec = importlib.util.spec_from_file_location("_launcher_under_test", ROOT / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def test_launcher_tells_children_to_write_utf8() -> None:
    """launcher 必须给子进程钉上 PYTHONIOENCODING=utf-8，且真的传给 Popen。

    子进程 stdout 指向文件时，Python 用区域编码（中文机 = GBK）而不是控制台代码页；
    日志文件却是按 UTF-8 开的 → `logs/backend.log` 会变成「ASCII 能看、中文乱码」。
    """
    mod = _load_launcher()
    env = mod._child_env()
    assert env.get("PYTHONIOENCODING") == "utf-8", "_child_env() 没有钉 UTF-8"
    # 原有环境变量不能被丢掉（PATH 丢了 node/python 都找不到）
    assert "PATH" in env or not os.environ.get("PATH")

    src = (ROOT / "launcher.py").read_text(encoding="utf-8")
    assert "env=child_env" in src, "Popen 没把 child_env 传给子进程"
    assert 'open(path, "a", encoding="utf-8"' in src, "日志文件不是按 UTF-8 打开的"


def test_child_writes_a_uniformly_utf8_log(tmp_path) -> None:
    """端到端：照 launcher 的方式起子进程，落下来的日志整体应是 UTF-8。

    修之前这里必挂：中文段是 GBK 字节，`read_text(encoding="utf-8")` 直接抛。
    """
    log_path = tmp_path / "backend.log"
    code = (
        "print('模板库迁移完成：回填 3 条 模板↔项目 关联');"
        "print('⚠ 如果这条能看见，说明编码通了');"
        "print('DONE')"
    )
    with open(log_path, "a", encoding="utf-8", errors="replace") as fh:
        p = subprocess.run(
            [sys.executable, "-c", code],
            stdout=fh,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=60,
        )
    assert p.returncode == 0, "子进程在写日志时挂了"
    text = log_path.read_text(encoding="utf-8")     # ← 定死 UTF-8，不许混字节
    assert "DONE" in text
    assert "模板↔项目" in text, "中文/符号段在 UTF-8 日志里读不回来，说明编码不一致"

