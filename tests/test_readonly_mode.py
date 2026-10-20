# -*- coding: utf-8 -*-
"""只读锁定：防止别人把数据玩坏。

用户 2026-10-20：「还得加点防呆，感觉有些呆逼会玩坏」

这层不靠用户小心，也不靠再多点几次确认框 —— 打开锁定后，后端对**任何写请求**
一律回 403。测试要钉住三件事，缺一条这个锁就是漏的：
  ① 写操作全被挡住（建、改、删、导入、生成、恢复备份）；
  ② 读操作照常（不然"只读"变成了"不可用"）；
  ③ 设置接口必须放行，否则锁上就再也解不开。
"""
from __future__ import annotations

import pytest


def _make_project(client, code: str = "PRJ-RO") -> tuple[int, int]:
    pid = client.post("/api/v1/projects", json={"code": code, "name": "只读测试"}).json()["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 5000.0},
    ).json()["id"]
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 5001.0},
    )
    return pid, pt


def _lock(client, on: bool = True) -> None:
    r = client.put("/api/v1/settings", json={"readonly_mode": on})
    assert r.status_code == 200, r.text
    assert r.json()["readonly_mode"] is on


# ─────────────────────────── 写：全挡 ───────────────────────────


def test_create_project_is_blocked(client) -> None:
    _lock(client, True)
    r = client.post("/api/v1/projects", json={"code": "PRJ-X", "name": "不该建出来"})
    assert r.status_code == 403, r.text
    assert "只读" in r.json()["detail"]


def test_create_record_is_blocked(client) -> None:
    pid, pt = _make_project(client)
    _lock(client, True)
    r = client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 5002.0},
    )
    assert r.status_code == 403


def test_delete_is_blocked(client) -> None:
    pid, pt = _make_project(client, "PRJ-RO2")
    _lock(client, True)
    r = client.delete(f"/api/v1/points/{pt}/hard", params={"force": "JJC1"})
    assert r.status_code == 403
    # 测点还在
    assert client.get(f"/api/v1/projects/{pid}/points").json()


def test_generate_is_blocked(client) -> None:
    pid, _ = _make_project(client, "PRJ-RO3")
    _lock(client, True)
    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-07-01", "end": "2026-07-10"},
    )
    assert r.status_code == 403


def test_restore_backup_is_blocked(client) -> None:
    """恢复备份是覆盖全库的破坏性操作，锁定时必须挡住。"""
    client.post("/api/v1/backup")
    recs = client.get("/api/v1/backup/list").json()
    if not recs:
        pytest.skip("没有可用备份记录")
    _lock(client, True)
    r = client.post(f"/api/v1/backup/{recs[0]['id']}/restore", params={"confirm": True})
    assert r.status_code == 403


# ─────────────────────────── 读：照常 ───────────────────────────


def test_reads_still_work(client) -> None:
    pid, pt = _make_project(client, "PRJ-RO4")
    _lock(client, True)

    assert client.get("/api/v1/projects").status_code == 200
    assert client.get(f"/api/v1/projects/{pid}/points").status_code == 200
    assert client.get(
        f"/api/v1/points/{pt}/computed", params={"series_type": "settlement", "component": "value"}
    ).status_code == 200
    assert client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"}).status_code == 200


# ─────────────────────────── 解锁的通路必须留着 ───────────────────────────


def test_settings_endpoint_stays_open(client) -> None:
    """锁上之后还能改设置 —— 否则解锁只能去改数据库。"""
    _lock(client, True)
    r = client.put("/api/v1/settings", json={"readonly_mode": False})
    assert r.status_code == 200
    assert r.json()["readonly_mode"] is False


def test_unlock_restores_writes(client) -> None:
    pid, pt = _make_project(client, "PRJ-RO5")
    _lock(client, True)
    assert client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-01", "value_mm": 5003.0},
    ).status_code == 403

    _lock(client, False)
    r = client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-01", "value_mm": 5003.0},
    )
    assert r.status_code in (200, 201), r.text


def test_locking_does_not_change_existing_data(client) -> None:
    """上锁本身不能碰数据（它只是拒绝后续写入）。"""
    pid, _ = _make_project(client, "PRJ-RO6")
    before = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"}).json()
    _lock(client, True)
    after = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"}).json()
    assert before["series"] == after["series"]
