# -*- coding: utf-8 -*-
"""归档项目不参与报警提示（用户 2026-11-16："归档的项目不要再报警提示了"）。

归档 = 这个工点已经结束。它的历史报警要能查（预警事件页、列表切「全部」），
但不该每天开机还弹在「需要关注」和顶部横幅上 —— 那会把真正要处理的那条挤下去。

守的几条：
- 预警汇总**照样返回**归档项目的状态，并带上 `is_active`（前端据此过滤）；
- 归档项目的 `has_alarm` 不被抹掉（数据是事实，只是不提示）；
- 关注项接口本来就不含归档项目（回归线）。
"""
from tests.conftest import put_control_rules


def _mk_project(client, code: str) -> tuple[int, int]:
    r = client.post("/api/v1/projects", json={"code": code, "name": code})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 100.0},
    )
    assert r.status_code == 201, r.text
    pt = r.json()["id"]
    put_control_rules(client, pid)
    return pid, pt


def _add_over_limit(client, pt: int) -> None:
    """写一条超限读数：V0=100mm、控制值 30mm，这里沉 35mm（读数 65mm）。"""
    r = client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-05-01", "value_mm": 65.0},
    )
    assert r.status_code == 201, r.text


def test_warning_summary_marks_archived_projects(client):
    """归档项目仍返回状态，但 is_active=False —— 前端靠这个字段过滤提示。"""
    live, live_pt = _mk_project(client, "在用基坑")
    dead, dead_pt = _mk_project(client, "已完工基坑")
    _add_over_limit(client, live_pt)
    _add_over_limit(client, dead_pt)

    r = client.delete(f"/api/v1/projects/{dead}")
    assert r.status_code == 200, r.text

    rows = {x["project_id"]: x for x in client.get("/api/v1/warning-summary").json()}
    assert rows[live]["is_active"] is True
    assert rows[dead]["is_active"] is False

    # 状态本身没被抹掉：数据是事实，只是不再拿去提示
    assert rows[dead]["has_control"] is True
    assert rows[live]["has_control"] is True


def test_attention_never_includes_archived(client):
    """关注项（数据可疑 / 该测了）本来就不含归档项目 —— 这是回归线。"""
    live, _ = _mk_project(client, "在用基坑")
    dead, _ = _mk_project(client, "已完工基坑")
    assert client.delete(f"/api/v1/projects/{dead}").status_code == 200

    ids = {x["project_id"] for x in client.get("/api/v1/project-attention").json()}
    assert live in ids
    assert dead not in ids
