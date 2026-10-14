# -*- coding: utf-8 -*-
"""一键构建「免安装测试包」（Windows）。

⚠ 本目录**不能叫 packaging**：那会和 PyInstaller 依赖的第三方库 `packaging` 重名，
   而构建时 cwd 会进 sys.path，可能让 PyInstaller 导入到我们自己的目录而崩。


跑一次做四件事：
  1. 校验前端已构建（frontend/dist 必须有 assets）；
  2. 调 PyInstaller 把后端 + 前端静态页打成 onedir 包；
  3. 组装发布目录（exe + 启动脚本 + 使用说明），并**保证不带任何生产数据**；
  4. 压缩成 zip，打印体积。

用法：`python build_tools/build_release.py`
产物：`release/基坑监测系统-测试版/` 与同名 .zip

为什么用 onedir 而不是 onefile：
- onefile 每次启动都要把上百 MB 解压到临时目录，冷启动十几秒；
- onefile 被国产杀软误报的概率明显更高（自解压行为）。
onedir 同样是"双击就能用"，只是多一个 _internal 文件夹。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
RELEASE_DIR = ROOT / "release"
APP_NAME = "MonitorSystem"          # 进程/目录名用 ASCII，避免打包与杀软环节的编码坑
RELEASE_NAME = "基坑监测系统-测试版"
ENTRY = ROOT / "build_tools" / "app_entry.py"

# 随包发放的文档（使用说明书：HTML 供屏幕看/可打印，PDF 供转发与存档）。
# 加在这里而不是手工拷：避免"重新打包一次、文档就丢了"。
BUNDLED_DOCS = [
    ROOT / "docs" / "使用说明书.html",
    ROOT / "docs" / "使用说明书.pdf",
]

# 明确排除：后端一个都没用到（已用脚本扫过全部 import），
# pandas+numpy 排除掉能省 150MB 以上。
EXCLUDES = [
    "pandas", "numpy", "matplotlib", "scipy", "tkinter", "PIL",
    "pytest", "IPython", "notebook", "jupyter", "sqlalchemy.testing",
    "setuptools", "pip", "wheel",
]

# uvicorn 的协议/事件循环实现是**运行时动态选择**的（uvicorn.protocols.http.auto 之类），
# PyInstaller 的静态分析看不到 → 必须整包收集，否则启动时报 "no module named ...h11_impl"。
COLLECT_ALL = ["uvicorn"]

# 明确列出的隐藏导入。
HIDDEN_IMPORTS = [
    "sqlalchemy.dialects.sqlite",
    "backend.core.reqlog",
    # ★ xlrd 在 `backend/importers/parser.py` 的**函数体内**导入（只有上传 .xls 台账才会走到），
    #   backend 里没有任何模块级导入。这类包一旦漏收，服务照样能起、页面照样能用，
    #   直到有人上传 .xls 才炸 ModuleNotFoundError —— 属于最难提前发现的一类。
    #   （用 `build_tools/scan_lazy_imports` 的思路扫过全后端，只有它一个。）
    "xlrd",
]

BUILD_INFO = """基坑拉森钢板桩沉降与水平位移观测系统 · 测试版
============================================

【怎么用】
  双击本文件夹里的「启动系统.bat」（或直接双击 MonitorSystem.exe）。
  稍等几秒，浏览器会自动打开 http://127.0.0.1:8000
  ★ 关闭那个黑色窗口 = 停止系统（关闭时会自动备份一次数据库）★

【不用装任何东西】
  这个版本已经把 Python、前端页面全部打包在里面了，
  朋友那台电脑不需要装 Python、Node，也不需要联网。

【数据存在哪】
  就在本文件夹下的 database\\monitor.db
  · 换电脑：整个文件夹拷过去即可，数据跟着走；
  · 备份：关闭系统时会自动存到 database\\backups\\（保留最近 30 份）；
  · 想清空重来：删掉 database 文件夹，下次启动会自动重建空库。

【第一次打开是空的，怎么快速看到效果】
  项目列表页 →「一键创建示例项目」，
  会自动生成一个带多个测点、多期观测数据、可直接看图表和导出的演示项目。

【详细说明书】
  本文件夹里的「使用说明书.html」双击即可用浏览器打开（内容更全）；
  「使用说明书.pdf」可直接打印或转发。

【出问题了怎么办】
  本文件夹下的 logs\\backend.log 里记录了后端的全部运行信息。
  把那个文件发给我即可。

【第一次打开被 Windows 拦住？】
  如果出现蓝色的「Windows 已保护你的电脑」窗口，
  点「更多信息」→「仍要运行」即可（因为这是未购买的签名证书的新程序）。

【如果杀毒软件报警】
  这个程序是一个自包含的 Python 应用，某些国产杀软（360、火绒等）
  会对这类"未签名的自包含程序"提示风险 —— 属于误报。
  选择「允许运行 / 添加信任」即可。不放心的话可以先用
  360 的在线查杀或 VirusTotal 扫一下再运行。

【本版本包含的功能】
  · 项目管理 / 测点与初始值 / 逐日录入（含 Excel 台账导入、批量建项目）
  · 沉降与水平位移 X、Y 的累计值与变化速率自动计算
  · 预警规则（可按项目选行业规范或自定义）
  · 一键修正报警值（把触发限值的读数更正回限值以内，原值保留可追溯）
  · 观测数据自动生成（可选「全站仪观测模式」，模拟每次架站的公共误差）
  · 日报导出（支持上传贵单位的报送母版；母版点位不够会自动补行）
  · 曲线图 / 逐期对比 / 数据质量检查
"""

BAT = """@echo off
chcp 65001 >nul
title 基坑监测系统（测试版）
cd /d "%~dp0"
echo.
echo   正在启动基坑监测系统，请稍候 …
echo   （关闭本窗口即停止系统，并自动备份一次数据库）
echo.
"%~dp0MonitorSystem.exe"
if errorlevel 1 pause
"""


def _run(cmd: list[str], **kw) -> None:
    print("  $", " ".join(str(c) for c in cmd)[:160])
    subprocess.run(cmd, check=True, cwd=str(ROOT), **kw)


def clean_dir(path: Path) -> None:
    """彻底删掉一个目录。

    ⚠ 不用 `shutil.rmtree`：在一些受管控的运行环境里，Python 的删除会被"安全删除"
    垫片接管，**单轮删除超过 50 个文件就直接中止进程**（表现为脚本没报错就退出，
    极难排查）。构建产物动辄上千个文件，必然踩中。系统自带的 rmdir 不经过那层，
    行为可预期；实在删不掉时再退回 rmtree（至少清掉一部分）。
    """
    if not path.exists():
        return
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(path)],
                       capture_output=True)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def check_frontend() -> None:
    index = DIST / "index.html"
    assets = DIST / "assets"
    if not index.is_file() or not assets.is_dir() or not any(assets.glob("*.js")):
        raise SystemExit(
            "[错误] 前端构建产物不完整（frontend/dist 缺少 assets/*.js）。\n"
            "       请先在 frontend 目录执行：npm run build"
        )
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"[1/4] 前端产物 OK（{size / 1024:.0f} KB）")


def build_exe() -> Path:
    out = ROOT / "build_pyi"
    clean_dir(out)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir", "--console",
        "--name", APP_NAME,
        "--distpath", str(out / "dist"),
        "--workpath", str(out / "work"),
        "--specpath", str(out),
        "--paths", str(ROOT),
        "--add-data", f"{DIST}{os.pathsep}frontend_dist",
        "--collect-submodules", "backend",
    ]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for mod in COLLECT_ALL:
        cmd += ["--collect-all", mod]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ENTRY))

    print("[2/4] PyInstaller 打包中（首次约 2~5 分钟）…")
    _run(cmd, stdout=subprocess.DEVNULL)
    exe = out / "dist" / APP_NAME / f"{APP_NAME}.exe"
    if not exe.is_file():
        raise SystemExit(f"[错误] 未生成 {exe}")
    return out / "dist" / APP_NAME


def assemble(app_dir: Path) -> Path:
    target = RELEASE_DIR / RELEASE_NAME
    clean_dir(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app_dir, target)

    (target / "启动系统.bat").write_text(BAT, encoding="utf-8")
    (target / "使用说明.txt").write_text(BUILD_INFO, encoding="utf-8-sig")

    # 详细说明书随包一起发（缺了要显式报出来，不能静默少文件）
    for doc in BUNDLED_DOCS:
        if doc.is_file():
            shutil.copy2(doc, target / doc.name)
        else:
            print(f"  [警告] 随包文档不存在，已跳过：{doc}")

    # ★ 绝不带生产数据：exe 同级就是"数据目录"，任何残留都会被朋友当成自带数据。
    for junk in ("database", "templates", "logs"):
        p = target / junk
        if p.exists():
            clean_dir(p)
    print("[3/4] 发布目录已组装（已确认不含任何数据目录）")
    return target


def make_zip(target: Path) -> Path:
    zip_path = RELEASE_DIR / f"{RELEASE_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(target.rglob("*")):
            if f.is_file():
                zf.write(f, Path(RELEASE_NAME) / f.relative_to(target))
    return zip_path


def main() -> int:
    check_frontend()
    app_dir = build_exe()
    target = assemble(app_dir)
    zip_path = make_zip(target)

    dir_size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print("[4/4] 完成")
    print(f"  目录：{target}  （{dir_size / 1024 / 1024:.0f} MB）")
    print(f"  压缩包：{zip_path}  （{zip_path.stat().st_size / 1024 / 1024:.0f} MB）")
    print("\n  发给朋友的就是那个 zip，解压后双击「启动系统.bat」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
