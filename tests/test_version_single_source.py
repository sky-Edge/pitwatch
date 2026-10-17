# -*- coding: utf-8 -*-
"""版本号必须只有一处出处。

用户 2026-10-17：
    「你先别动手 先把版本号做好 搞错了 我好退版本」

这里守三件事：
① `VERSION` 文件与 `backend/core/version.py` 里的常量一致（配对的东西最容易只改一头）；
② 版本号格式合法，且 `CHANGELOG.md` 里有对应条目（不然"退到哪版"说不清）；
③ `/api/health` 真的把版本号报出来 —— 界面顶栏显示的就是它。
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.core.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_version_file_matches_code() -> None:
    """根目录 VERSION 与代码常量必须一致。

    为什么不干脆只留一个：打包成 exe 后根目录的 VERSION 不一定会被带进包，代码里必须留常量；
    而给人看的、给脚本读的又需要一个纯文本文件。两边都要，就得有人守着。
    """
    file_ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert file_ver == __version__, (
        f"VERSION 文件是 {file_ver}，代码里是 {__version__} —— 改版本号要两处一起改"
        "（正确做法：python tools/version.py save \"说明\"，它会一起写）"
    )


def test_version_format_and_changelog() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), f"版本号格式不对：{__version__}"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## v{__version__}" in changelog, (
        f"CHANGELOG.md 里没有 v{__version__} 这一条 —— 退版本时没人知道那版改了什么"
    )


def test_health_exposes_version(client) -> None:
    """界面顶栏显示的版本号来自这里；少了它用户没法确认"我跑的是哪一版"。"""
    d = client.get("/api/health").json()
    assert d.get("version") == __version__
