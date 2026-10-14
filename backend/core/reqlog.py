# -*- coding: utf-8 -*-
"""请求错误日志：把 4xx/5xx 落盘，供事后排查。

【为什么需要它】uvicorn 的访问日志默认只打到**启动它的那个控制台窗口**，
窗口一关就没了。2026-10-05 用户说「刚刚有 400 报错」，而我：
· 复现了首页/导出页/日期面板/记录页/生成数据/预警页/测点页 —— 一条 4xx 都没有；
· 想看真实日志 —— 后端当时已经关了，窗口没了，**日志根本不存在**。
结果是只能猜。落盘之后，同样的情况读一眼文件就能定位。

同样遵守"会写盘的目录必须可隔离"那条铁律：目录取自 `settings.log_dir`
（可用 `MONITOR_LOG_DIR` 覆盖，conftest 指向临时目录）。
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from backend.core.config import settings

_LOG_NAME = "requests-error.log"
_MAX_BYTES = 2 * 1024 * 1024        # 单文件上限，超了轮转一份（只留 .1，够追溯就行）


def log_path() -> Path:
    return settings.log_dir / _LOG_NAME


def _rotate_if_needed(p: Path) -> None:
    try:
        if p.exists() and p.stat().st_size > _MAX_BYTES:
            p.replace(p.with_name(p.name + ".1"))
    except OSError:
        pass


def log_failed_request(*, method: str, path: str, query: str, status: int,
                       seconds: float, client: str | None = None) -> None:
    """记一条失败请求。

    **只记元信息**（方法/URL/状态/耗时/来源），不去读响应体 ——
    中间件里读 body 会把流消耗掉，属于为了排查把功能弄坏。
    URL 里已经带上了查询参数，定位"哪个接口、什么参数"够用了。
    """
    try:
        url = f"{path}?{query}" if query else path
        line = (f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S}  {status}  {method} {url}  "
                f"{seconds * 1000:.0f}ms  {client or '-'}\n")
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(p)
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        # 记日志失败绝不能影响正常响应 —— 这是日志，不是业务
        pass


def read_recent(limit: int = 100) -> list[str]:
    """读回最近若干条（给诊断接口用）。文件不存在就返回空列表。"""
    p = log_path()
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]
