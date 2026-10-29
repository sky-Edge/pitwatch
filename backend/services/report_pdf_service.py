# -*- coding: utf-8 -*-
"""Excel → PDF：「打印日报」用。

【为什么在服务端转，而不是前端重画一遍】
日报是套了母版的正式文件 —— 封面、签字栏、多个 Sheet、合并单元格、列宽、字体，
全是用户在 Excel 里一点点调出来的。前端用 HTML 重画，画得再像也是"另一份东西"：
监理手上那份和系统里这份不是同一个版式，迟早被挑刺。

所以走最笨也最准的路：**让 LibreOffice 把 Excel 原样渲染成 PDF**，
打印出来的就是母版本身的样子。

【几个必须处理的实际问题】
1. 并发：LibreOffice 共用用户配置目录时会互相锁死（第二个进程直接卡住），
   所以每次转换都给一个独立的 `-env:UserInstallation`。
2. 字体：镜像里不装中文字体，渲染出来全是方块 —— 这是容器里最常见的事故。
   见 Dockerfile 里的 `fonts-noto-cjk` / `fonts-wqy-zenhei`。
3. 超时：首次启动 soffice 要几秒，大表格更久；给 120 秒，超时明确报错。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class PdfConvertError(RuntimeError):
    """转换失败（环境缺失 / 超时 / LibreOffice 没产出文件）。"""


def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def is_available() -> bool:
    """这台机器能不能转 PDF（本地 Windows 通常没装，云端镜像内置）。"""
    return _soffice() is not None


def xlsx_to_pdf(xlsx_bytes: bytes, *, timeout: int = 120) -> bytes:
    """把 xlsx 字节流转成 PDF 字节流。失败抛 PdfConvertError。"""
    exe = _soffice()
    if not exe:
        raise PdfConvertError(
            "这台机器上没有 LibreOffice，无法把日报转成 PDF。"
            "云端镜像已内置；在自己电脑上跑的话需要先装 LibreOffice。"
        )

    with tempfile.TemporaryDirectory(prefix="pitwatch_pdf_") as td:
        work = Path(td)
        src = work / "report.xlsx"
        src.write_bytes(xlsx_bytes)

        cmd = [
            exe,
            "--headless",
            "--norestore",
            "--nolockcheck",
            # 独立配置目录 —— 并发转换时共用 profile 会互相锁死
            f"-env:UserInstallation=file://{(work / 'profile').as_posix()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work),
            str(src),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise PdfConvertError(f"转换超时（超过 {timeout} 秒），表格可能太大") from exc
        except OSError as exc:
            raise PdfConvertError(f"启动 LibreOffice 失败：{exc}") from exc

        out = work / "report.pdf"
        if not out.exists():
            detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "ignore").strip()[:300]
            raise PdfConvertError(f"LibreOffice 没有生成 PDF（退出码 {proc.returncode}）：{detail}")

        return out.read_bytes()
