# -*- coding: utf-8 -*-
"""测试：物理删除（force 确认+级联+自动备份）与 record-dates 预警日期标记。"""
import sqlite3
from pathlib import Path

import pytest

from backend.core.config import settings


@pytest.fixture(autouse=True)
def _clean_backup_dir():
    settings.ensure_dirs()
    for f in settings.backup_dir.glob("*.db"):
        f.unlink(missing_ok=True)
    yield
    for f in settings.backup_dir.glob("*.db"):
        f.unlink(missing_ok=True)


def _db_file() -> Path:
    url = settings.database_url
    assert url.startswith("sqlite:///")
    return Path(url.removeprefix("sqlite:///"))


def _setup(client):
    """项目 PRJ001 + 2 测点 + 沉降观测数据 + 规则。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "删除测试"}).json()
    pid = p["id"]
    j1 = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "both"}).json()
    j2 = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC2", "point_type": "settlement"}).json()
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
            ],
            "source_note": "t",
        },
    )
    # JJC1 沉降触发预警（累计 -37mm）
    for d, v in [("2026-07-30", 4907.0), ("2026-07-31", 4906.0), ("2026-08-01", 4870.0)]:
        client.post(
            f"/api/v1/points/{j1['id']}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
        )
    client.post(
        f"/api/v1/points/{j2['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 5200.0},
    )
    return pid, j1["id"], j2["id"]


def test_point_hard_delete_requires_force(client):
    """测点物理删除：force 不匹配 → 400。"""
    pid, j1, _ = _setup(client)
    r = client.delete(f"/api/v1/points/{j1}/hard")
    assert r.status_code == 400
    assert "force" in r.json()["detail"]
    # 数据仍在
    assert len(client.get(f"/api/v1/projects/{pid}/points").json()) == 2


def test_point_hard_delete_cascades(client):
    """测点物理删除：级联删除观测记录 + 自动备份。"""
    pid, j1, _ = _setup(client)
    r = client.delete(f"/api/v1/points/{j1}/hard", params={"force": "JJC1"})
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    # 测点没了
    pts = client.get(f"/api/v1/projects/{pid}/points").json()
    assert len(pts) == 1
    assert pts[0]["point_no"] == "JJC2"
    # 观测记录级联删除（查库）
    con = sqlite3.connect(_db_file())
    n = con.execute("SELECT COUNT(*) FROM observation_records").fetchone()[0]
    con.close()
    assert n == 1  # 只剩 JJC2 那条
    # 自动备份已生成
    assert len(list(settings.backup_dir.glob("*.db"))) >= 1


def test_project_hard_delete(client):
    """项目物理删除：force=项目编号，级联全部。"""
    pid, _, _ = _setup(client)
    # force 错误 → 400
    assert client.delete(f"/api/v1/projects/{pid}/hard", params={"force": "XXX"}).status_code == 400
    # 正确 force
    r = client.delete(f"/api/v1/projects/{pid}/hard", params={"force": "PRJ001"})
    assert r.status_code == 200
    body = r.json()
    assert body["points_removed"] == 2
    # 项目及其数据都没了
    con = sqlite3.connect(_db_file())
    tables = {
        "projects": "SELECT COUNT(*) FROM projects",
        "points": "SELECT COUNT(*) FROM measurement_points",
        "records": "SELECT COUNT(*) FROM observation_records",
        "rules": "SELECT COUNT(*) FROM warning_rules WHERE project_id IS NOT NULL",
        # 规则独立成"规则库"之后，规则行的归属看 set_id（project_id 已废弃）。
        # 这个项目只被它自己用 → 那套规则应随项目一起删掉，不能留孤儿套。
        "rule_sets": "SELECT COUNT(*) FROM warning_rule_sets",
        "rule_bindings": "SELECT COUNT(*) FROM warning_rule_set_projects",
    }
    for t, sql in tables.items():
        n = con.execute(sql).fetchone()[0]
        assert n == 0, f"{t} 未清空: {n}"
    con.close()


def test_data_count(client):
    """项目数据量统计。"""
    pid, _, _ = _setup(client)
    r = client.get(f"/api/v1/projects/{pid}/data-count")
    d = r.json()
    assert d["points"] == 2
    assert d["records"] == 4
    assert d["has_data"] is True


def test_record_dates_marks_warning_days(client):
    """record-dates：alarm_dates / control_dates 标记预警日期。"""
    pid, _, _ = _setup(client)
    d = client.get(f"/api/v1/projects/{pid}/record-dates").json()
    assert "2026-08-01" in d["dates"]
    assert "2026-07-30" in d["baseline_dates"]
    # JJC1 8-01 累计 -37 ≥30 → control（在 30~40 之间），速率 -36/2=-18 → alarm 判定？
    # 速率 -36/2 = -18 < -12? 绝对值 18 ≥12 → alarm。所以 8-01 应在 alarm_dates
    assert "2026-08-01" in d["alarm_dates"], d
