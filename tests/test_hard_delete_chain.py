# -*- coding: utf-8 -*-
"""回归：含 superseded 更正链的点/项目物理删除不应 FK 500。"""
import pytest

from backend.core.config import settings


@pytest.fixture(autouse=True)
def _clean():
    settings.ensure_dirs()
    for f in settings.backup_dir.glob("*.db"):
        f.unlink(missing_ok=True)
    yield


def _make_project_with_correction_chain(client):
    """建项目 + 点 + 记录 → 更正一次 → 产生 superseded 链。返回 (pid, point_id)。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "更正链删除"}).json()
    pid = p["id"]
    pt = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "settlement"}).json()
    rid = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 4906.0},
    ).json()["id"]
    # 更正（走 correct 接口，旧记录 superseded_by_id 指向新记录）
    r = client.post(f"/api/v1/records/{rid}/correct", json={"value_mm": 4905.0})
    assert r.status_code == 200, r.text
    return pid, pt["id"]


def _assert_chain_exists(pid: int):
    """确认库中确有 superseded 链。"""
    import sqlite3

    from backend.core.config import settings as s

    url = s.database_url
    con = sqlite3.connect(url.removeprefix("sqlite:///"))
    n = con.execute(
        "SELECT COUNT(*) FROM observation_records WHERE superseded_by_id IS NOT NULL"
    ).fetchone()[0]
    con.close()
    assert n >= 1, "测试前置失败：未产生 superseded 链"


def test_hard_delete_point_with_chain(client):
    pid, pt_id = _make_project_with_correction_chain(client)
    _assert_chain_exists(pid)
    resp = client.delete(f"/api/v1/points/{pt_id}/hard", params={"force": "JJC1"})
    assert resp.status_code == 200, f"删点失败: {resp.text}"
    assert resp.json()["deleted"] is True


def test_hard_delete_project_with_chain(client):
    pid, _ = _make_project_with_correction_chain(client)
    _assert_chain_exists(pid)
    resp = client.delete(f"/api/v1/projects/{pid}/hard", params={"force": "PRJ001"})
    assert resp.status_code == 200, f"删项目失败: {resp.text}"
    assert resp.json()["deleted"] is True
