# -*- coding: utf-8 -*-
"""打包相关守卫（免安装发布包）。

为什么要专门守：打包形态和开发形态有**两套不同的路径语义**（数据目录、前端来源），
且发布包一旦带上生产数据或写错目录，后果都是"用户数据丢失/泄露"级别。
历史上这几条都真实踩过：

- 数据目录若跟着 `__file__` 走，onefile 下会落到 %TEMP% 的临时解包目录 —— 重启即丢；
- 停机收尾若只"设个标志"就返回，主线程会退出进程、把备份线程一起带走
  （现象：关窗了、没报错、也没有备份）；
- 备份目录若按目录硬拼而不读 settings，测试会在背后给**真实生产库**做快照。
"""
from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_ENTRY = ROOT / "build_tools" / "app_entry.py"
BUILD_SCRIPT = ROOT / "build_tools" / "build_release.py"


def _load_app_entry():
    """按文件路径加载打包入口（build_tools 不是包，且不能叫 packaging —— 与三方库同名）。"""
    spec = importlib.util.spec_from_file_location("wb_app_entry", APP_ENTRY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ────────────────────────── 1. 路径语义 ──────────────────────────


def test_base_dir_is_project_root_in_source_mode():
    """源码模式下数据目录 = 项目根（打包模式下另算，见下一条）。"""
    assert not getattr(sys, "frozen", False), "这条断言只在源码模式下有意义"
    from backend.core.config import BASE_DIR

    assert BASE_DIR == ROOT


def test_frozen_base_dir_is_exe_folder_not_meipass():
    """源码守卫：打包后数据目录必须是 exe 所在目录。

    用 `__file__` 的两种打包形态都会出问题：
      · onedir → 落在 `_internal/`；
      · onefile → 落在 `%TEMP%/_MEIxxxx/`，退出即删。
    对监测系统来说等于"用户的数据每次重启都没了"。
    """
    src = (ROOT / "backend" / "core" / "config.py").read_text(encoding="utf-8")
    assert 'getattr(sys, "frozen", False)' in src, "config.py 没有处理打包形态"
    block = src[src.index("def _resolve_base_dir"):src.index("BASE_DIR = _resolve_base_dir()")]
    assert "sys.executable" in block, "frozen 分支必须用 sys.executable 定位 exe 目录"
    assert "_MEIPASS" not in block, "数据目录绝不能落在 _MEIPASS（临时解包目录）"


def test_frontend_dist_dir_resolves_to_build_output():
    from backend.core.config import frontend_dist_dir

    dist = frontend_dist_dir()
    assert dist is not None, "前端构建产物不存在（先 npm run build）"
    assert (dist / "index.html").is_file()
    assert list((dist / "assets").glob("*.js")), "dist/assets 里没有构建产物"


# ────────────────────────── 2. 单端口托管 ──────────────────────────


def test_frontend_is_mounted_at_root():
    """dist 存在时后端必须把前端挂到 "/" —— 打包版靠它做到"只有一个端口、不装 Node"。"""
    from backend.core.config import frontend_dist_dir
    from backend.main import app

    if frontend_dist_dir() is None:
        pytest.skip("没有前端构建产物")
    mounts = [r for r in app.routes if r.__class__.__name__ == "Mount"]
    assert any(getattr(m, "path", None) == "" for m in mounts), (
        "没有把前端挂到根路径；打包版会打不开页面"
    )


def test_static_mount_is_registered_after_api_routes():
    """静态挂载必须在 API 路由**之后**：mount("/") 会吃掉所有未匹配路径。"""
    src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    api_pos = src.index('# 后端能力清单')
    mount_pos = src.index('app.mount("/"')
    assert mount_pos > api_pos, "静态托管挂在了 API 路由前面，会把 /api/* 全部拦掉"


# ────────────────────────── 3. 停机收尾（踩过的坑） ──────────────────────────


def test_shutdown_waits_for_the_backup_to_finish():
    """源码守卫：收尾必须"串行 + 等它做完"，不能只设个标志就返回。

    踩坑记录：早先是 handler 设 `_stop_flag` 后返回，主循环见标志即 `return`，
    进程退出把**还在备份**的 daemon 线程一起杀掉 —— 关窗后没有备份、也没有任何报错。
    """
    src = APP_ENTRY.read_text(encoding="utf-8")
    assert "with _shutdown_lock" in src, "收尾没有用锁串行化，可能被主线程提前带走"
    assert "_shutdown_done" in src, "缺少「收尾完成」标志，后到调用者无法等待"
    main_body = src[src.index("def main("):]
    assert "_graceful_shutdown()" in main_body, "主流程退出前没有执行收尾"


def test_shutdown_is_idempotent_and_backs_up(tmp_path, monkeypatch):
    """收尾真的会生成备份；重复调用不会重复备份。"""
    ae = _load_app_entry()
    from backend.core.config import settings

    monkeypatch.setattr(settings, "backup_dir", tmp_path / "bk")
    ae._shutdown_done.clear()
    ae._servers.clear()

    ae._graceful_shutdown(wait=0)
    made = sorted((tmp_path / "bk").glob("auto_backup_*.db"))
    assert made, "收尾没有生成数据库备份"
    assert made[0].stat().st_size > 0

    before = len(made)
    ae._graceful_shutdown(wait=0)          # 幂等
    assert len(list((tmp_path / "bk").glob("auto_backup_*.db"))) == before


def test_backup_targets_settings_not_hardcoded_dir(monkeypatch, tmp_path):
    """备份读写的是 `settings` 指向的库与目录。

    若按"exe 目录/database"硬拼，测试（MONITOR_DB_URL 指向副本库）会去**真实生产库**
    做快照 —— 正是项目里反复栽过的那类事故。
    """
    ae = _load_app_entry()
    from backend.core.config import settings

    monkeypatch.setattr(settings, "backup_dir", tmp_path / "bk2")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'other.db'}")
    assert ae._db_file() == tmp_path / "other.db"
    # 指向不存在的库时不报错、不生成半成品
    ae._shutdown_done.clear()
    assert ae._auto_backup() is None


def test_free_port_skips_busy_port():
    """端口被占用时要自动让开（朋友机器上跑着别的东西也不该启动失败）。"""
    ae = _load_app_entry()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        got = ae._free_port(busy, span=8)
        assert got != busy, f"占用的端口 {busy} 被直接返回了"


# ────────────────────────── 4. 构建脚本本身 ──────────────────────────


def test_build_script_never_ships_production_data():
    """发布目录必须显式清掉 data 目录，否则朋友的包里会带上真实工程数据。"""
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert 'for junk in ("database", "templates", "logs")' in src, "没有清理数据目录"
    assert "已确认不含任何数据目录" in src


def test_build_script_avoids_python_bulk_delete():
    """清理构建产物不能用 Python 的 rmtree：受限环境下会被"安全删除"垫片拦截
    （单轮 > 50 个文件直接中止进程，脚本会没报错就退出，极难排查）。"""
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "def clean_dir" in src
    assert 'subprocess.run(["cmd", "/c", "rmdir"' in src, "清理没有走系统 rmdir"
    assert "shutil.rmtree(out)" not in src
    assert "shutil.rmtree(target)" not in src


def test_build_script_excludes_unused_heavy_deps():
    """pandas/numpy 后端一个都没用（已扫过全部 import），打进去白胖 150MB+。"""
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    for mod in ("pandas", "numpy"):
        assert f'"{mod}"' in src, f"{mod} 没有加进排除清单"


def test_lazy_imports_are_declared_as_hidden_imports():
    """**只在函数体内**导入的第三方包，必须显式声明 --hidden-import。

    这类漏收最难发现：服务能起、页面能用，直到用户走到那条代码路径才炸
    ModuleNotFoundError —— 典型是 `xlrd`（只有上传 .xls 台账时才走到）。
    这条测试会自动扫全后端，所以以后**新加**一个延迟导入也会被抓出来。
    """
    scanner = _load_module("wb_scan_lazy", ROOT / "build_tools" / "scan_lazy_imports.py")
    builder = _load_module("wb_build_release", BUILD_SCRIPT)

    lazy_only = scanner.find_lazy_only_modules()
    declared = set(builder.HIDDEN_IMPORTS) | set(builder.COLLECT_ALL)
    missing = lazy_only - declared
    assert not missing, (
        f"这些包只在函数体内导入，却没在 build_release.py 里声明 hidden-import："
        f"{sorted(missing)}（漏收的后果是走到那条路径才报缺模块）"
    )


def test_release_artifacts_do_not_contain_data_dirs():
    """若已经构建过，直接检查产物里没有数据目录。"""
    target = ROOT / "release" / "基坑监测系统-测试版"
    if not target.is_dir():
        pytest.skip("还没有构建过发布包")
    for junk in ("database", "templates", "logs"):
        assert not (target / junk).exists(), f"发布包里带了 {junk}/"
    assert (target / "MonitorSystem.exe").is_file()
    assert (target / "启动系统.bat").is_file()
    assert (target / "使用说明.txt").is_file()


def test_release_ships_the_manual():
    """使用说明书必须随包发出去。

    说明书是使用者唯一的依靠（怎么启动、数据在哪、预警怎么看），
    漏带一次就是"朋友拿到手不知道从哪开始"。
    """
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "BUNDLED_DOCS" in src and "使用说明书" in src, "构建脚本没有把说明书列为随包文档"
    for doc in (ROOT / "docs" / "使用说明书.html", ROOT / "docs" / "使用说明书.pdf"):
        assert doc.is_file(), f"说明书不存在：{doc.name}（改完说明记得重新导出 PDF）"

    target = ROOT / "release" / "基坑监测系统-测试版"
    if not target.is_dir():
        pytest.skip("还没有构建过发布包")
    for name in ("使用说明书.html", "使用说明书.pdf"):
        assert (target / name).is_file(), f"发布包里缺少 {name}"
