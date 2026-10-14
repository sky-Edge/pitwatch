# -*- coding: utf-8 -*-
"""Phase 7 测试：预警事件落库重建 + 数据质量检查。"""
import pytest


def _setup(client):
    """建项目 + JJC1(both) + 8 期沉降数据（最后两期累计超控制值）。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "测试"}).json()
    pid = p["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "both"},
    ).json()
    # 沉降序列：4907 → 逐期小幅下降，最后累计 -37mm（超 control 30）
    values = [
        ("2026-07-30", 4907.0),
        ("2026-07-31", 4906.0),
        ("2026-08-01", 4898.0),
        ("2026-08-02", 4892.0),
        ("2026-08-03", 4885.0),
        ("2026-08-04", 4880.0),
        ("2026-08-05", 4875.0),
        ("2026-08-06", 4870.0),  # 累计 -37mm
    ]
    for d, v in values:
        client.post(
            f"/api/v1/points/{pt['id']}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
        )
    # 位移 X：3 期正常
    for d, v in [("2026-07-30", 3413055112.0), ("2026-07-31", 3413055110.0), ("2026-08-01", 3413055108.0)]:
        client.post(
            f"/api/v1/points/{pt['id']}/records",
            json={"series_type": "displacement", "component": "X", "obs_date": d, "value_mm": v},
        )
    return pid, pt["id"]


def _apply_rules(client, pid):
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
            ],
            "source_note": "测试",
        },
    )


def test_recompute_generates_events(client):
    """重算生成预警事件（派生表落库，含 recompute_batch）。"""
    pid, _ = _setup(client)
    _apply_rules(client, pid)

    resp = client.post(f"/api/v1/projects/{pid}/warning-events/recompute")
    assert resp.status_code == 200
    data = resp.json()
    assert data["events_generated"] > 0
    assert len(data["recompute_batch"]) == 12

    events = client.get(f"/api/v1/projects/{pid}/warning-events").json()
    assert len(events) == data["events_generated"]
    # 事件应有累计触发
    assert any(e["trigger_type"] == "cumulative" for e in events)
    # 事件点位名正确
    assert all(e["point_no"] == "JJC1" for e in events)


def test_recompute_is_idempotent(client):
    """重算幂等：重复执行不堆积事件。"""
    pid, _ = _setup(client)
    _apply_rules(client, pid)
    client.post(f"/api/v1/projects/{pid}/warning-events/recompute")
    n1 = len(client.get(f"/api/v1/projects/{pid}/warning-events").json())
    client.post(f"/api/v1/projects/{pid}/warning-events/recompute")
    n2 = len(client.get(f"/api/v1/projects/{pid}/warning-events").json())
    assert n1 == n2 > 0


def test_quality_detects_suspect(client):
    """突变检测：单期 |ΔV| 超阈值 → suspect 提示（只标记不删除）。"""
    pid, pt = _setup(client)
    # 故意录入一个突变值（前一期 4875 → 突变 4800，单期 -75mm）
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-07", "value_mm": 4800.0},
    )
    resp = client.get(f"/api/v1/projects/{pid}/quality-issues")
    assert resp.status_code == 200
    data = resp.json()
    assert data["suspect_count"] >= 1
    s = next(x for x in data["suspects"] if x["point_no"] == "JJC1")
    assert "突变" in s["note"]


def test_quality_detects_missing(client):
    """缺测统计。"""
    pid, pt = _setup(client)
    # 补一条缺测（value_mm=None）
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-07", "value_mm": None},
    )
    resp = client.get(f"/api/v1/projects/{pid}/quality-issues")
    data = resp.json()
    assert data["missing_count"] >= 1
    assert any(x["obs_date"] == "2026-08-07" for x in data["missing"])


def test_quality_threshold_configurable(client):
    """突变阈值可配置：sign_config.data_quality.mutation_threshold_mm。"""
    pid, _ = _setup(client)
    # 默认阈值 10mm：-1mm 变化不触发
    resp = client.get(f"/api/v1/projects/{pid}/quality-issues")
    assert resp.json()["mutation_threshold_mm"] == 10.0
    # 改为 0.5mm 后 -1mm 也触发
    client.put(
        f"/api/v1/projects/{pid}",
        json={"sign_config": {"data_quality": {"mutation_threshold_mm": 0.5}}},
    )
    resp = client.get(f"/api/v1/projects/{pid}/quality-issues")
    assert resp.json()["mutation_threshold_mm"] == 0.5
    assert resp.json()["suspect_count"] > 0
