# -*- coding: utf-8 -*-
"""聚合接口缓存：该快的时候快，该变的时候必须变。

为什么单独钉一遍
----------------
项目列表页每次打开都会打 `/warning-summary`、`/project-briefs`、`/project-attention`，
三个接口都要把全部历史过一遍计算引擎（实测各 200~520ms）。加了进程内缓存之后，
**唯一不能出的错就是脏读**：录了一条超限数据，首页还显示"正常"。

缓存失效不用在几十个写入口各插一行（漏一个就是脏读），而是看数据库文件的版本戳：
SQLite 每次提交都会更新主库或 `-wal` 的 mtime。这里就把这条约定钉住 ——
"写完之后下一次读必须看到新结果"。
"""
from __future__ import annotations

import pytest

from backend.core import cache


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清一次：避免上一个用例的缓存串到下一个。"""
    cache.clear()
    yield
    cache.clear()


def _mk_project(client, name: str = "缓存测试") -> tuple[int, int]:
    p = client.post(
        "/api/v1/projects",
        json={"code": name, "name": name, "sign_config": {"settlement": {"positive_means": "down"}}},
    ).json()
    pid = p["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "T01", "point_type": "settlement", "initial_value_mm": 100.0},
    ).json()
    return pid, pt["id"]


def _set_control(client, pid: int, mm: float) -> None:
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {
                    "indicator": "settlement",
                    "level": "control",
                    "threshold_cumulative_mm": mm,
                    "threshold_rate_mm_per_day": 10.0,
                    "is_active": True,
                }
            ]
        },
    )


def _row(client, pid: int) -> dict:
    return [r for r in client.get("/api/v1/warning-summary").json() if r["project_id"] == pid][0]


def test_cache_hit_returns_same_result(client):
    pid, _pt = _mk_project(client)
    first = _row(client, pid)
    second = _row(client, pid)
    assert first == second


def test_new_record_invalidates_cache(client):
    """录一条超限读数后，汇总必须立刻反映出来 —— 不能卡在上一份缓存上。"""
    pid, pt = _mk_project(client)
    _set_control(client, pid, 5.0)

    before = _row(client, pid)
    assert before["has_control"] is False and before["has_alarm"] is False

    r = client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-05-01", "value_mm": 90.0},
    )
    assert r.status_code == 201, r.text

    after = _row(client, pid)
    assert after["has_control"] is True, "新增超限记录后缓存没失效 —— 首页会显示旧状态"


def test_rule_change_invalidates_cache(client):
    """改规则（不放新数据）也要让缓存失效：判据变了，结论就变了。"""
    pid, pt = _mk_project(client)
    _set_control(client, pid, 5.0)
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-05-01", "value_mm": 90.0},
    )
    assert _row(client, pid)["has_control"] is True

    # 把控制值放宽到 50mm → 同样的读数就不该再判预警
    _set_control(client, pid, 50.0)
    assert _row(client, pid)["has_control"] is False, "改规则后缓存没失效"


def test_briefs_and_attention_also_invalidate(client):
    """另外两个聚合接口用同一套机制，一起验一遍。"""
    pid, pt = _mk_project(client, "缓存测试2")
    _set_control(client, pid, 5.0)
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-05-01", "value_mm": 90.0},
    )
    headline1 = [b for b in client.get("/api/v1/project-briefs").json() if b["project_id"] == pid][0]["headline"]
    assert headline1  # 有结论

    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-05-02", "value_mm": 80.0},
    )
    headline2 = [b for b in client.get("/api/v1/project-briefs").json() if b["project_id"] == pid][0]["headline"]
    assert headline2 != headline1, "新数据进来后项目结论还是旧的"


def test_cache_module_reports_version():
    """版本戳必须能拿到（拿不到就不是 SQLite，缓存会自动降级为不缓存）。"""
    v = cache.data_version()
    assert v is None or isinstance(v, tuple)


def test_projects_with_counts_matches_data_count(client):
    """`/projects?with_counts=true` 的计数必须和单项目 `/data-count` 一致。

    列表页改成"一次拿全"之后，项目行上的「N 个测点 · M 条数据」就来自这个接口。
    口径一旦和删除确认弹窗（走 `/data-count`）不一致，用户会看到两个不同的数字。
    """
    pid, pt = _mk_project(client, "计数一致性")
    for d, v in (("2026-05-01", 95.0), ("2026-05-02", 94.0)):
        client.post(
            f"/api/v1/points/{pt}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
        )

    rows = client.get("/api/v1/projects", params={"with_counts": "true"}).json()
    me = [r for r in rows if r["id"] == pid][0]
    single = client.get(f"/api/v1/projects/{pid}/data-count").json()

    assert me["points"] == single["points"] == 1
    assert me["records"] == single["records"] == 2

    # 不传 with_counts 时不该多这两个字段（老客户端行为不变）
    plain = client.get("/api/v1/projects").json()
    one = [r for r in plain if r["id"] == pid][0]
    assert one.get("points") is None
