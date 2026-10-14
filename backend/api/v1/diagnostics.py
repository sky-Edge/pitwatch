# -*- coding: utf-8 -*-
"""诊断接口（**只读**）。

为什么要有：排查现场问题时（"刚刚有个 400"），最需要的是**当时的请求记录**。
uvicorn 的访问日志只在控制台窗口里，窗口一关就没了；这里把落盘的失败请求读回来，
不必让用户去翻窗口截图。

对外只暴露"出了哪些错"，不涉及业务数据，也没有任何写操作。
"""
from fastapi import APIRouter, Query

from backend.core import reqlog

diag_router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@diag_router.get("/recent-errors")
def recent_errors(limit: int = Query(100, ge=1, le=500)):
    """最近若干条失败请求（4xx/5xx），按时间正序。"""
    lines = reqlog.read_recent(limit)
    return {
        "count": len(lines),
        "log_file": str(reqlog.log_path()),
        "lines": lines,
    }
