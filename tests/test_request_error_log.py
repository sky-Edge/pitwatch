# -*- coding: utf-8 -*-
"""失败请求落盘（2026-10-05 用户报「刚刚有 400」时加的）。

【为什么有这个功能】uvicorn 的访问日志只打进**启动它的那个控制台窗口** ——
用户说"刚刚有个 400"，我复现了一轮（首页/导出页/日期面板/记录页/生成/预警/测点）
一条 4xx 都没有，想看真实日志时后端已经关了，**日志根本不存在**，只能靠猜。
落盘之后同样的情况读文件就能定位。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.core import reqlog
from backend.core.config import settings


def _lines() -> list[str]:
    return reqlog.read_recent(1000)


def test_failed_request_is_written_to_disk(client):
    """4xx 要落盘，并且带上完整 URL —— 没有 URL 就等于没记。"""
    before = len(_lines())
    # `/holidays/{year}` 的年份超范围 → 400，只读、无副作用
    r = client.get("/api/v1/holidays/1999")
    assert r.status_code == 400, r.text

    lines = _lines()
    assert len(lines) == before + 1, f"400 没有落盘：{lines[-3:]}"
    last = lines[-1]
    assert "400" in last and "/api/v1/holidays/1999" in last, last
    assert "GET" in last, last


def test_query_string_is_recorded(client):
    """查询参数必须记下来 —— 同一个接口传不同参数，出问题的是哪一次全靠它。"""
    client.get("/api/v1/holidays/1999?from=probe")
    assert "from=probe" in _lines()[-1]


def test_successful_request_is_not_recorded(client):
    """200 不进这个文件（它是"错误日志"，不是访问日志，否则很快被正常流量淹掉）。"""
    before = len(_lines())
    assert client.get("/api/health").status_code == 200   # 注意：健康检查不在 /api/v1 下
    assert len(_lines()) == before, "正常请求被误记进错误日志了"


def test_422_is_recorded_too(client):
    """参数校验失败（422）也要记 —— 用户界面上它和 400 长得一模一样。"""
    before = len(_lines())
    r = client.post("/api/v1/projects/9999/generate-data",
                    params={"start": "2026-09-02", "end": "2026-09-01"})
    assert r.status_code >= 400
    assert len(_lines()) > before, "422 没被记录"


def test_diagnostics_endpoint_reads_them_back(client):
    """诊断接口能把落盘的记录读回来（排查时不必让用户去翻窗口）。"""
    client.get("/api/v1/holidays/1999")
    body = client.get("/api/v1/diagnostics/recent-errors").json()
    assert body["count"] > 0
    assert any("/api/v1/holidays/1999" in ln for ln in body["lines"])
    assert body["log_file"].endswith("requests-error.log")


def test_log_dir_is_isolated_in_tests():
    """铁律：会写盘的目录必须指向临时目录，绝不能是生产目录。

    这份日志是拿来排查现场问题的，被测试污染就失去意义；
    反过来，测试也不该往用户的 logs/ 里写东西。
    """
    assert "monitor_test_" in str(settings.log_dir), (
        f"测试的日志目录没有隔离：{settings.log_dir}"
    )


def test_launcher_writes_logs_to_files():
    """源码守卫：启动脚本必须把后端/前端的输出落到 logs/ 下。

    否则又回到"窗口一关就查不到" —— 这正是加这个功能的原因。
    """
    root = Path(__file__).resolve().parent.parent
    src = (root / "launcher.py").read_text(encoding="utf-8")
    assert 'log_dir / "backend.log"' in src or "backend.log" in src, "后端输出没有落盘"
    assert "frontend.log" in src, "前端输出没有落盘"
    assert "stdout=backend_log" in src, "后端进程没有接上日志文件"


def test_error_middleware_is_registered():
    """源码守卫：中间件必须挂在 app 上（写了函数没注册等于没写）。"""
    from backend.main import app

    names = [m.cls.__name__ for m in app.user_middleware]
    assert any("BaseHTTPMiddleware" in n or "_log_failed_requests" in n for n in names), (
        f"失败请求中间件没有注册：{names}"
    )
