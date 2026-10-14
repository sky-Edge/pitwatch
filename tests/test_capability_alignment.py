# -*- coding: utf-8 -*-
"""前后端「能力自检」对齐守卫。

前端 `App.tsx` 的 REQUIRED_BACKEND_FEATURES 与后端 `main.py` 的 BACKEND_FEATURES
启动时会互相比对，少一项就弹「后端服务是旧版本，请重启」。

两个方向都会出问题：
- 前端要求了后端**根本没声明**的能力 → 每次启动都弹"旧版本"，用户重启一百遍也没用，
  提示把人引到无意义的重启上；
- 后端加了能力却忘了让前端要求它 → 用户不重启也没提示，界面是新的、行为是旧的。

这里焊死第一条（可判定的那条）：**前端要求的每一项，后端必须声明过**。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "backend" / "main.py"
APP = ROOT / "frontend" / "src" / "App.tsx"

_FEATURE = re.compile(r"['\"]([a-z0-9_]{4,})['\"]")


def _list_items(src: str, anchor: str) -> set[str]:
    block = src.split(anchor, 1)[1].split("]", 1)[0]  # 取列表内容（到第一个 ] 为止）
    items: set[str] = set()
    for line in block.splitlines():
        code = line.split("#", 1)[0]      # 去掉行尾注释
        code = code.split("//", 1)[0]     # 去掉行尾注释（前端写法）
        items |= set(_FEATURE.findall(code))
    return items


def _backend_features() -> set[str]:
    return _list_items(MAIN.read_text(encoding="utf-8"), "BACKEND_FEATURES = [")


def _required_features() -> set[str]:
    return _list_items(APP.read_text(encoding="utf-8"), "REQUIRED_BACKEND_FEATURES = [")


def test_lists_are_not_empty() -> None:
    """解析本身先自证有效：两个列表都得读出东西来，否则下面的断言等于没写。"""
    assert len(_backend_features()) >= 10
    assert len(_required_features()) >= 8


def test_frontend_required_features_are_all_advertised_by_backend() -> None:
    missing = _required_features() - _backend_features()
    assert not missing, (
        f"前端要求、后端没声明：{sorted(missing)} —— 启动时会弹「后端是旧版本」，"
        "但后端其实已经是最新的，用户重启也没用。"
    )
