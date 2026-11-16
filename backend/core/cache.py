# -*- coding: utf-8 -*-
"""进程内只读结果缓存 —— 数据没变就不重算。

为什么要它
----------
项目列表页一打开会并发打三个聚合接口：`/warning-summary`、`/project-briefs`、
`/project-attention`。这三个都要把**所有项目、所有测点、全部历史记录**过一遍
计算引擎（为的是取"最新一期"那一个数），实测各 200~520ms，加起来一秒多。
而这个结果在"没有新数据进来"时是**完全不变**的。

为什么会变
----------
只有写操作会改变它：录数据、导入台账、改规则、改测点、生成数据。
不用在几十个写入口里各插一行"清缓存"（漏一个就是脏读），改成看**数据库文件的
版本戳**：SQLite 每次提交都会更新 `-wal`（或主库）的 mtime。读操作不改文件，
写操作必然改 —— 天然可靠，且一条 SQL 都不用发。

跨进程
------
uvicorn 起了多个 worker 时，每个进程各有一份缓存。它们各自看同一个文件 mtime，
写入后都会自然失效，不会出现"某个 worker 一直吐旧数据"。

不缓存什么
----------
只缓存"所有项目汇总"这类**只读、无参数、算得起但结果稳定**的接口。
单项目的计算序列、报表导出不在这里 —— 那些要么参数多，要么本身就要即时性。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.core.config import settings

#: 缓存最多留多少项。聚合接口就那么几个，64 足够；超了按写入时间淘汰最老的。
_MAX_ENTRIES = 64

_lock = threading.Lock()
#: key -> (数据版本, 写入时刻, 值)
_store: dict[tuple, tuple[tuple, float, Any]] = {}


def _sqlite_paths() -> tuple[Path, Path] | None:
    """当前数据库的主文件与 WAL 文件（非 SQLite 或路径解析失败返回 None）。"""
    url = settings.database_url or ""
    if not url.startswith("sqlite"):
        return None
    # sqlite:////data/database/monitor.db → /data/database/monitor.db
    # sqlite:///rel.db                     → rel.db
    raw = url.split("sqlite:///", 1)[-1]
    if not raw:
        return None
    main = Path(raw)
    return main, main.with_name(main.name + "-wal")


def data_version() -> tuple | None:
    """数据库版本戳：任一文件变化即视为"数据变了"。

    用 `st_mtime_ns` + `st_size` 两个一起看：mtime 精度够高（纳秒），
    但某些文件系统在极端情况下会做 mtime 合并，加上 size 更稳。
    """
    paths = _sqlite_paths()
    if paths is None:
        return None
    main, wal = paths
    stamps: list[tuple[str, int, int]] = []
    for p in (main, wal):
        try:
            st = p.stat()
        except OSError:
            continue  # WAL 可能不存在（刚 checkpoint 过）
        stamps.append((p.name, st.st_mtime_ns, st.st_size))
    if not stamps:
        return None
    return tuple(stamps)


def cached(namespace: str, args: tuple, compute: Callable[[], Any], ttl: float = 120.0) -> Any:
    """取缓存，没有或数据变了就重算。

    `ttl` 只是兜底（防止某些极端文件系统时间戳不动）。正常情况下靠 `data_version`
    失效，所以设得比较宽松。
    """
    version = data_version()
    if version is None:
        return compute()  # 非 SQLite：老老实实每次算

    key = (namespace, args)
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit is not None and hit[0] == version and (now - hit[1]) < ttl:
            return hit[2]

    value = compute()

    with _lock:
        _store[key] = (version, now, value)
        if len(_store) > _MAX_ENTRIES:
            # 按写入时间淘汰最老的（聚合接口数量很少，不需要 LRU 的复杂度）
            oldest = sorted(_store.items(), key=lambda kv: kv[1][1])[: len(_store) - _MAX_ENTRIES]
            for k, _v in oldest:
                _store.pop(k, None)
    return value


def clear() -> None:
    """清空缓存（测试与"强制刷新"用）。"""
    with _lock:
        _store.clear()


def stats() -> dict:
    """给诊断接口用：当前缓存了哪些命名空间。"""
    with _lock:
        return {
            "entries": len(_store),
            "keys": sorted({k[0] for k in _store}),
            "db_version": data_version(),
        }
