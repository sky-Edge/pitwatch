# -*- coding: utf-8 -*-
"""打包版（exe）启动入口。

和开发用的 `launcher.py` 的区别：
- **不再启动前端 dev server**：前端构建产物已打进包里，由后端在同一端口发出去
  （所以朋友那台电脑不需要装 Node，也只有一个地址要记）；
- **端口被占用时自动换端口**，不直接失败 —— 朋友机器上跑着什么占了 8000 也不该卡住；
- 数据库/模板/日志一律落在 **exe 同级目录**（换台电脑解压即用，卸载=删文件夹）。

调试提示：本文件也可以直接用 Python 跑（`python packaging/app_entry.py`），
此时 `sys.frozen` 为假，数据会落在仓库根目录 —— 与打包版行为一致，便于排错。
"""
from __future__ import annotations

import ctypes
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Windows 控制台默认 GBK，打印中文提示 + uvicorn 日志会乱码/报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _base_dir() -> Path:
    """发布根目录：打包后 = exe 所在目录（与 backend/core/config.py 的口径一致）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# 源码模式直接 `python build_tools/app_entry.py` 时，sys.path[0] 是 build_tools/，
# 里面没有 backend 包 —— 不补这一行会 ImportError，然后卡在"按回车退出"上，
# 现象是"跑起来没反应"。打包版由引导器设置好路径，不受影响。
if not getattr(sys, "frozen", False):
    _root = str(_base_dir())
    if _root not in sys.path:
        sys.path.insert(0, _root)


def _free_port(preferred: int, span: int = 20) -> int:
    """从 preferred 开始找一个没被占用的端口。"""
    for port in range(preferred, preferred + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex((HOST, port)) != 0:
                return port
    raise RuntimeError(f"{preferred}~{preferred + span - 1} 之间没有可用端口")


def _wait_ready(url: str, timeout: float = 60.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def _db_file() -> Path | None:
    """从 `settings.database_url` 解析出 SQLite 文件路径。

    为什么不直接拼 `database_dir / "monitor.db"`：`database_url` **可以被
    `MONITOR_DB_URL` 覆盖**（自动化测试就是靠它指向副本库）。按目录硬拼的话，
    测试会在背后去读真实生产库并给它做快照 —— 正是项目里反复栽过的那类事故。
    """
    from backend.core.config import settings

    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    raw = url.split("sqlite:///", 1)[-1]
    return Path(raw) if raw else None


def _auto_backup() -> str | None:
    """退出前快照数据库（沿用开发版 launcher 的做法：保留最近 30 份）。

    ⚠ 备份目录取 `settings.backup_dir`，不自己拼 `exe 目录/database/backups`：
    settings 已经处理了打包（frozen）与本地两种情形，而且**可被环境变量覆盖** ——
    自动化测试正是靠这一点把快照写进临时目录，否则每跑一次测试就往真实数据目录塞一份。
    """
    from backend.core.config import settings

    src = _db_file()
    if src is None or not src.exists():
        print(f"[提示] 未找到数据库文件（{src}），跳过备份。")
        return None
    try:
        bak = settings.backup_dir
        bak.mkdir(parents=True, exist_ok=True)
        dest = bak / f"auto_backup_{time.strftime('%Y%m%d_%H%M%S')}.db"
        con = sqlite3.connect(str(src))
        try:
            # VACUUM INTO：把 WAL 里尚未 checkpoint 的数据一并写进快照。
            # ⚠ 不用绑定参数（`VACUUM INTO ?`）—— 该语句对绑定参数的支持在不同
            #   SQLite 版本上不一致，直接转义单引号更稳；路径统一用正斜杠。
            target = str(dest.as_posix()).replace("'", "''")
            con.execute(f"VACUUM INTO '{target}'")
        finally:
            con.close()
        for old in sorted(bak.glob("auto_backup_*.db"))[:-30]:
            old.unlink(missing_ok=True)
        return dest.name
    except Exception as exc:
        # 早先这里是 `except: return None` —— 备份悄悄不生成、用户毫不知情。
        # 备份是"关窗时最需要"的动作，失败必须说出来。
        print(f"[警告] 自动备份失败：{type(exc).__name__}: {exc}")
        return None


_servers: list = []              # [uvicorn.Server, ...]（停机时要通知的对象）
_stop_started = threading.Event()   # 停机已开始（主循环据此退出）
_shutdown_lock = threading.Lock()   # 保证收尾只做一次，且后到者会等它做完
_shutdown_done = threading.Event()
_keepalive: list = []            # 防 GC：控制台 handler 必须被引用住
_BASE: Path | None = None


def _graceful_shutdown(wait: float = 1.0) -> None:
    """停机收尾：停服务 → 放掉数据库连接 → 备份。**幂等且并发安全**。

    两个坑都在这里踩过（2026-10-05）：
    1. 不能只设个标志、指望主循环去收尾 —— Windows 在关窗/注销时只给进程几秒钟，
       到点强杀；handler 里必须**直接**把收尾做完。
    2. 只加一个"已收尾"标志仍然不够：主线程看到标志就退出进程了，会把**还在备份**的
       daemon 收尾线程一起带走 —— 现象是"关窗了、没报错、也没有备份"。
       所以用锁把整段收尾串起来：后到的调用者在这里**等前一个做完**才返回。
    """
    _stop_started.set()
    with _shutdown_lock:                 # 第二个调用者在此等待收尾真正完成
        if _shutdown_done.is_set():
            return
        try:
            for srv in _servers:
                try:
                    srv.should_exit = True
                except Exception:
                    pass
            time.sleep(wait)             # 给 uvicorn 一点时间结束在途请求
            try:
                from backend.core.db import engine

                engine.dispose()         # 关闭连接会触发 WAL checkpoint
            except Exception:
                pass
            if name := _auto_backup():
                print(f"\n已自动备份数据库：{name}", flush=True)
        finally:
            _shutdown_done.set()


def _install_console_handler() -> None:
    """关窗 / Ctrl+C 时执行完整收尾（含自动备份）。"""
    if not sys.platform.startswith("win"):
        return
    try:
        PHANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def _handler(ctrl_type: int) -> int:
            if ctrl_type in (0, 1, 2):  # CTRL_C / CTRL_BREAK / CTRL_CLOSE
                _graceful_shutdown()
                return 1
            return 0

        handler = PHANDLER(_handler)
        _keepalive.append(handler)  # 防 GC
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
    except Exception:
        pass


def _arg_value(name: str, default: int) -> int:
    """取 `--name 值` 形式的整数参数（自动化实测用，正常双击不会传）。"""
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return default
    return default


def main() -> int:
    global _BASE
    base = _base_dir()
    _BASE = base
    # 自动化实测时要能关掉"自动弹浏览器"（否则每跑一次弹一个窗口），
    # 也能指定端口避开别的服务、以及在 N 秒后自动走一遍停机流程。
    # 正常双击不传参数，行为不变。
    open_browser = "--no-browser" not in sys.argv
    preferred_port = _arg_value("--port", DEFAULT_PORT)
    stop_after = _arg_value("--stop-after", 0)

    print("=" * 64)
    print("  基坑拉森钢板桩沉降与水平位移观测系统")
    print("  （测试版 · 免安装）")
    print("=" * 64)

    try:
        port = _free_port(preferred_port)
    except RuntimeError as exc:
        print(f"[错误] {exc}")
        input("按回车键退出 …")
        return 1
    if port != preferred_port:
        print(f"[提示] 端口 {preferred_port} 已被占用，自动改用 {port}。")

    # 依赖在包内已冻结；这里只做一次导入，出错要给看得懂的信息
    try:
        import uvicorn

        from backend.main import app
    except Exception as exc:  # 打包缺件时才会走到
        print(f"[错误] 程序组件加载失败：{exc}")
        input("按回车键退出 …")
        return 1

    config = uvicorn.Config(
        app, host=HOST, port=port, log_level="info", access_log=True, log_config=None
    )
    server = uvicorn.Server(config)
    _servers.append(server)
    _install_console_handler()

    print(f"[1/2] 正在启动服务 {HOST}:{port} …")
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://{HOST}:{port}/"
    if _wait_ready(url + "api/health"):
        print("[2/2] 服务已就绪，正在打开浏览器 …")
    else:
        print("[2/2] 服务启动较慢，请手动在浏览器打开下面地址。")

    print("-" * 64)
    print(f"  浏览器地址：{url}")
    print(f"  数据目录　：{base}")
    print("  · 数据存在上面这个文件夹里（数据库  database/monitor.db）")
    print("  · ★ 关闭本窗口 = 停止系统，并自动备份一次数据库 ★")
    print("  · 换电脑：整个文件夹拷过去，双击同一个 exe 即可")
    print("-" * 64)

    try:
        if open_browser:
            webbrowser.open(url)
    except Exception:
        pass

    if stop_after:
        # 自检用：N 秒后走一遍和"关窗"完全相同的停机流程，
        # 这样自动化测试能验证收尾（含自动备份）真的执行了。
        def _later() -> None:
            time.sleep(stop_after)
            print(f"\n[自检] {stop_after}s 到，执行一次完整停机流程 …", flush=True)
            _graceful_shutdown()

        threading.Thread(target=_later, daemon=True).start()

    try:
        while thread.is_alive() and not _stop_started.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[提示] 正在停止服务 …")

    # 幂等：控制台事件已经跑过就直接返回；正在跑则**在这里等它做完**再退出进程
    _graceful_shutdown()
    print("系统已停止，窗口可安全关闭。")
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()  # PyInstaller 下防止子进程重复执行入口
    sys.exit(main())
