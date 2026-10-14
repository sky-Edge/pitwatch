# -*- coding: utf-8 -*-
"""基坑监测系统 · 一键启动器（Windows 控制台版）。

用法：双击「启动系统.bat」即可（或直接运行本脚本）。

特性：
- 同时启动 FastAPI 后端(8000) + Vite 前端(5173)；
- 自动打开浏览器 http://127.0.0.1:5173；
- 前后端子进程与启动器共享同一控制台窗口：
  ★ 关闭这个黑色窗口 → 全部服务随之停止（无需再手动逐个关闭）★
- 若端口被占用（服务已在跑），会提示并退出，不会产生僵尸进程。
"""
from __future__ import annotations

import ctypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
# 后端用「启动本脚本的解释器」运行 —— 因此启动前必须确认它装齐了依赖，
# 否则 uvicorn 会以非零码退出、且错误藏在子进程输出里，用户只看到前端打不开。
PYTHON = sys.executable

# 输出的编码策略：①**绝不因为"打不出来"而中断**（errors=replace）；
# ②统一 UTF-8 —— 控制台在 Windows 上本来就是 UTF-8（PEP 528 用 WriteConsoleW），
#   而一旦有人把输出重定向到文件（`启动系统.bat > log.txt`），默认就会掉回 GBK。
# 与打包入口 `build_tools/app_entry.py` 的 `reconfigure(encoding="utf-8", errors="replace")`
# 保持同一口径。
# 背景（2026-10-09 真实事故）：后端因为一句 print 里有个 GBK 编不出的 `↔` 直接起不来。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

# 后端运行所需的最小依赖集（缺任一即无法启动）
_REQUIRED_MODULES = ("fastapi", "uvicorn", "sqlalchemy", "pydantic")


def _child_env() -> dict:
    """给子进程的环境变量。

    ★ 为什么要显式指定 PYTHONIOENCODING ★
    子进程的 stdout 被重定向到**文件**（不再是控制台），此时 Python 不看控制台代码页，
    而是用 Windows 的区域默认编码 —— 中文机器上是 **GBK**。而我们把日志文件按
    **UTF-8** 打开（见 `_open_log`），两边不一致的后果是：
    `logs/backend.log` 里 ASCII 段正常、中文段是 GBK 字节 → 用记事本/编辑器打开就是
    「半截能看半截乱码」（2026-10-09 实测），严重时还直接抛 UnicodeEncodeError。
    显式钉成 UTF-8，让「写日志的一方」和「开文件的一方」用同一种编码。
    """
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


# 前端入口脚本（vite 的 bin）。注意：不要在这里写死 node.exe 的版本路径 ——
# 托管运行时目录会随 WorkBuddy 升级改名，写死会让启动器直接 FileNotFoundError。
VITE_JS = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"


def _find_node() -> str | None:
    """自动定位 node.exe。

    不写死版本号：托管的 node 版本目录会随工具升级改名
    （例如 22.22.2-2 → 22.22.2-3），写死会导致启动器直接崩。
    依次尝试：
      1) 托管目录下任意 node.exe（版本号最高的优先，current 兜底）
      2) 项目本地 node（便携部署场景）
      3) 系统 PATH 中的 node
    """
    import shutil

    exe_name = "node.exe" if sys.platform.startswith("win") else "node"

    def _pick(candidates: list[Path]) -> str | None:
        files = [p for p in candidates if p.is_file()]
        if not files:
            return None

        def _key(p: Path):
            ver = p.parent.name
            nums = [int(n) for n in re.findall(r"\d+", ver)] or [0]
            # 「current」这类无版本号的软链排最后
            return (bool(re.fullmatch(r"\d[\d.\-]*", ver)), nums)

        return str(sorted(files, key=_key)[-1])

    # 1) WorkBuddy 托管运行时
    versions_dir = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if versions_dir.is_dir():
        hit = _pick(list(versions_dir.glob(f"*/{exe_name}")))
        if hit:
            return hit

    # 2) 项目本地（把 node 一起打包的便携部署）
    local = BASE_DIR / "node" / exe_name
    if local.is_file():
        return str(local)

    # 3) 系统 PATH
    return shutil.which("node")


NODE = _find_node()


def _missing_modules(interpreter: str) -> list[str]:
    """检查解释器是否装齐后端依赖。返回缺失模块名列表（空=齐全）。

    用子进程探测而不是在当前进程 import：本脚本可能被任意 Python 启动，
    而依赖只保证装在"正确的那个"解释器里；当前进程 import 会污染自身。
    """
    code = "import importlib.util as u,sys;" + ";".join(
        f"sys.stdout.write('' if u.find_spec('{m}') else '{m} ')" for m in _REQUIRED_MODULES
    )
    try:
        out = subprocess.run(
            [interpreter, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return list(_REQUIRED_MODULES)
    if out.returncode != 0:
        return list(_REQUIRED_MODULES)
    return (out.stdout or "").split()

HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_PORT = 5173

# ---- 控制台关闭事件处理（用户点窗口 ✕ / Ctrl+C / Ctrl+Break）----
# 当关闭 cmd 窗口时 Windows 会给本进程发 CTRL_CLOSE_EVENT，
# 这里主动先清理子进程再退出，确保"关窗即停全部服务"。
_children: list[subprocess.Popen] = []
_stop_event = threading.Event()
_console_handler_ref = []  # 防止 handler 被 GC


def _auto_backup() -> str | None:
    """退出前自动快照数据库（服务仍在跑时执行，快照最干净）。

    - VACUUM INTO 生成完整快照（含 WAL 中未 checkpoint 的数据）；
    - 存入 database/backups/auto_backup_*.db；
    - 自动保留最近 30 份，超出即清理最旧（防磁盘膨胀）。
    """
    try:
        import sqlite3

        src = BASE_DIR / "database" / "monitor.db"
        bak_dir = BASE_DIR / "database" / "backups"
        if not src.exists():
            return None
        bak_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = bak_dir / f"auto_backup_{ts}.db"
        con = sqlite3.connect(str(src))
        try:
            con.execute(f"VACUUM INTO '{dest.as_posix()}'")
        finally:
            con.close()
        # 保留最近 30 份自动备份
        autos = sorted(bak_dir.glob("auto_backup_*.db"))
        for old in autos[:-30]:
            old.unlink(missing_ok=True)
        return dest.name
    except Exception:
        return None


def _shutdown_all():
    """退出收尾：终止所有子进程（幂等）。自动备份由调用方在合适时机执行。"""
    for p in _children:
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    time.sleep(0.8)
    for p in _children:
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
    _stop_event.set()


def _install_console_handler():
    """注册 Windows 控制台事件处理器（CTRL_C/CTRL_BREAK/CTRL_CLOSE）。"""
    if not sys.platform.startswith("win"):
        return
    try:
        PHANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def _handler(ctrl_type: int) -> int:
            if ctrl_type in (0, 1, 2):  # CTRL_C / CTRL_BREAK / CTRL_CLOSE
                _shutdown_all()
                return 1  # 已处理：随后主循环检测 stop_event 退出
            return 0

        handler = PHANDLER(_handler)
        _console_handler_ref.append(handler)  # 保活
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
    except Exception:
        pass  # 非 Windows 或失败时不影响主流程


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def wait_http_ready(url: str, timeout: float = 30.0) -> bool:
    """轮询等待 HTTP 服务就绪。"""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    print("=" * 62)
    print("  基坑拉森钢板桩沉降与水平位移观测系统 · 一键启动")
    print("=" * 62)

    # ---- 依赖预检：把「找不到文件」翻译成人话，不抛裸 traceback ----
    if not NODE:
        print("[错误] 未找到 node.exe —— 无法启动前端。")
        print("       请安装 Node.js，或在 WorkBuddy 中重新装回 node 运行时。")
        return 1
    if not Path(NODE).exists():
        print(f"[错误] node.exe 路径不存在：{NODE}")
        return 1
    if not VITE_JS.exists():
        print("[错误] 前端依赖未安装（缺少 node_modules/vite）。")
        print(f"       请在 {FRONTEND_DIR} 下执行：npm install")
        return 1

    # Python 侧预检：后端用的是启动本脚本的解释器，缺依赖会静默失败
    missing = _missing_modules(PYTHON)
    if missing:
        print("[错误] 当前 Python 缺少后端依赖：")
        print(f"       解释器：{PYTHON}")
        print(f"       缺失：{'、'.join(missing)}")
        print()
        print("       请任选一种方式解决：")
        print(f'         1) 装依赖："{PYTHON}" -m pip install -r requirements.txt')
        print("         2) 改用已装好依赖的解释器启动本脚本")
        print("         3) 直接双击「启动系统.bat」（会自动挑一个可用的 Python）")
        return 1

    # 端口预检
    if port_busy(BACKEND_PORT):
        print(f"[错误] 端口 {BACKEND_PORT} 已被占用 —— 系统可能已在运行？")
        print("       请先关闭现有服务（或检查占用进程）后重试。")
        return 1
    if port_busy(FRONTEND_PORT):
        print(f"[错误] 端口 {FRONTEND_PORT} 已被占用，请先释放。")
        return 1

    procs: list[subprocess.Popen] = []

    # ---- 日志落盘 ----
    # 为什么必须落盘：uvicorn 的访问日志默认只打到这个控制台窗口，窗口一关就没了。
    # 用户报「刚刚有个 400」时无从查证（2026-10-05 真实发生过，只能靠猜）。
    # 追加模式 + 超限轮转，保留历史。
    # ⚠ 文件按 UTF-8 打开，子进程也必须按 UTF-8 吐字（见 `_child_env`），
    #   否则中文段会以 GBK 字节落进 UTF-8 文件 → 日志半截乱码。
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    def _open_log(name: str):
        path = log_dir / name
        try:
            if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                path.replace(path.with_name(path.name + ".1"))
        except OSError:
            pass
        return open(path, "a", encoding="utf-8", errors="replace")

    backend_log = _open_log("backend.log")
    frontend_log = _open_log("frontend.log")
    child_env = _child_env()

    # ---- 启动后端 ----
    print(f"[1/3] 启动后端服务  http://{HOST}:{BACKEND_PORT}")
    print(f"      日志：{log_dir / 'backend.log'}（出问题时把这里发我）")
    backend = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "backend.main:app", "--host", HOST, "--port", str(BACKEND_PORT)],
        cwd=str(BASE_DIR),
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        env=child_env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    procs.append(backend)
    _children.append(backend)

    # ---- 启动前端 ----
    print(f"[2/3] 启动前端服务  http://{HOST}:{FRONTEND_PORT}")
    frontend = subprocess.Popen(
        [NODE, str(VITE_JS), "--host", HOST, "--port", str(FRONTEND_PORT)],
        cwd=str(FRONTEND_DIR),
        stdout=frontend_log,
        stderr=subprocess.STDOUT,
        env=child_env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    procs.append(frontend)
    _children.append(frontend)
    print(f"      node: {NODE}")

    # 注册关窗处理器（必须在子进程启动后、进入等待前）
    _install_console_handler()

    # ---- 等待就绪 ----
    try:
        if not wait_http_ready(f"http://{HOST}:{BACKEND_PORT}/api/health"):
            print("[警告] 后端 30 秒内未就绪，请查看上方日志。")
        if not wait_http_ready(f"http://{HOST}:{FRONTEND_PORT}/"):
            print("[警告] 前端 30 秒内未就绪，请查看上方日志。")
    except Exception:
        pass

    print("[3/3] 系统已就绪，正在打开浏览器 …")
    print("-" * 62)
    print("  使用提示：")
    print("  · 浏览器访问 http://127.0.0.1:5173")
    print("  · ★ 关闭本黑色窗口 = 停止全部服务，并自动备份数据库 ★")
    print("  · 自动备份保存于 database/backups/（保留最近 30 份）")
    print("-" * 62)
    try:
        webbrowser.open(f"http://{HOST}:{FRONTEND_PORT}/")
    except Exception:
        pass

    # 等待：任一进程退出 / 收到关窗信号 / Ctrl+C 即触发收尾
    try:
        while not _stop_event.is_set():
            exited = [p.poll() for p in procs]
            if any(e is not None for e in exited):
                print("\n[提示] 某个服务已退出，正在保存快照并清理 …")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[提示] 收到停止信号，正在保存快照并关闭服务 …")

    # 收尾：自动备份 + 结束所有子进程
    backup_name = _auto_backup()
    _shutdown_all()
    if backup_name:
        # ⚠ 这里原来写的是 "✓" —— GBK 控制台编不出来会抛 UnicodeEncodeError，
        # 把启动器自己带崩（备份明明成功了，用户却看到一堆乱码 + 进程退出）。
        # 控制台上只用 GBK 里有的符号（√ 是有的，✓ 不是）。
        print(f"\n√ 已自动备份数据库：database/backups/{backup_name}")
    print("全部服务已停止，窗口可安全关闭。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
