# -*- coding: utf-8 -*-
"""启动器运行时探测的回归测试。

背景：launcher.py 曾被写死 `binaries/node/versions/22.22.2-2/node.exe`，
托管目录升级改名为 22.22.2-3 后直接 FileNotFoundError 崩溃。此后同类问题又出现过一次。
本测试锁住「不写死版本号」这一行为，防止回归。
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "launcher.py"


def _load_launcher():
    """以模块方式载入 launcher（它 import 时不执行 main）。"""
    spec = importlib.util.spec_from_file_location("_launcher_under_test", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lm():
    return _load_launcher()


def test_no_hardcoded_version_path_in_source():
    """源码里不得出现写死的版本号路径（注释里提到版本号做说明是允许的）。"""
    src = LAUNCHER.read_text(encoding="utf-8")
    # 匹配形如 versions/22.22.2-3/ 或 versions\3.13.12\ 的实际路径片段
    hardcoded = re.findall(r"versions[/\\]\d[\d.\-]*[/\\]", src)
    assert not hardcoded, f"发现写死的版本路径：{hardcoded}"


def test_no_hardcoded_absolute_python_path_in_source():
    """源码里不得出现写死的绝对解释器路径。"""
    src = LAUNCHER.read_text(encoding="utf-8")
    assert "envs/default/Scripts/python.exe" not in src
    assert re.search(r'[A-Za-z]:\\\\?Users', src) is None, "源码中存在写死的用户绝对路径"


def test_find_node_prefers_highest_version(lm, tmp_path, monkeypatch):
    """托管目录里多个版本时，应取版本号最高的那个。"""
    exe = "node.exe" if sys.platform.startswith("win") else "node"
    app = tmp_path / "app"
    app.mkdir()

    # 直接构造宿主目录结构：<home>/.workbuddy/binaries/node/versions/<ver>/<exe>
    home = tmp_path / "home"
    versions = home / ".workbuddy" / "binaries" / "node" / "versions"
    for ver in ("22.22.2-2", "22.22.2-3", "22.0.1"):
        d = versions / ver
        d.mkdir(parents=True)
        (d / exe).write_text("", encoding="utf-8")

    monkeypatch.setattr(lm, "BASE_DIR", app)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    got = lm._find_node()
    assert got is not None
    assert Path(got).parent.name == "22.22.2-3", f"未取到最高版本：{got}"


def test_find_node_ignores_versionless_dir(lm, tmp_path, monkeypatch):
    """「current」这类无版本号目录应排在带版本号目录之后（只作兜底）。"""
    exe = "node.exe" if sys.platform.startswith("win") else "node"
    app = tmp_path / "app"
    app.mkdir()
    home = tmp_path / "home"
    versions = home / ".workbuddy" / "binaries" / "node" / "versions"
    for ver in ("22.22.2-3", "current"):
        d = versions / ver
        d.mkdir(parents=True)
        (d / exe).write_text("", encoding="utf-8")

    monkeypatch.setattr(lm, "BASE_DIR", app)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    got = lm._find_node()
    assert got is not None
    assert Path(got).parent.name == "22.22.2-3"


def test_find_node_falls_back_to_local_node(lm, tmp_path, monkeypatch):
    """托管目录不存在时，应回退到项目本地 node/。"""
    exe = "node.exe" if sys.platform.startswith("win") else "node"
    app = tmp_path / "app"
    (app / "node").mkdir(parents=True)
    (app / "node" / exe).write_text("", encoding="utf-8")

    monkeypatch.setattr(lm, "BASE_DIR", app)
    # 指向一个不存在的 home，确保托管目录探测落空
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nowhere"))

    assert lm._find_node() == str(app / "node" / exe)


def test_find_node_returns_none_when_nothing_available(lm, tmp_path, monkeypatch):
    """哪都没有时应返回 None（由 main 给出人话提示），而不是抛异常。"""
    monkeypatch.setattr(lm, "BASE_DIR", tmp_path / "app")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nowhere"))
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert lm._find_node() is None


def test_missing_modules_detects_absent_module(lm):
    """探测函数应能识别出未安装的模块（用不可能存在的名字验证）。"""
    missing = lm._missing_modules(sys.executable)
    # 真实解释器上 fastapi 等可能装或不装，重点验证不会抛异常且返回 list
    assert isinstance(missing, list)

    lm._REQUIRED_MODULES = ("__definitely_not_a_real_module_xyz__",)
    try:
        assert lm._missing_modules(sys.executable) == ["__definitely_not_a_real_module_xyz__"]
    finally:
        lm._REQUIRED_MODULES = ("fastapi", "uvicorn", "sqlalchemy", "pydantic")


def test_missing_modules_handles_broken_interpreter(lm):
    """解释器不可用时，应保守返回「全部缺失」而不是崩溃。"""
    missing = lm._missing_modules("__no_such_interpreter_xyz__")
    assert missing  # 非空即表示被判定为不可用


def test_python_is_not_hardcoded(lm):
    """PYTHON 必须取自当前解释器（sys.executable），不得写死。"""
    assert lm.PYTHON == sys.executable
