# -*- coding: utf-8 -*-
"""Calculation API + 预警状态 API 集成测试（经 FastAPI 全链路）。"""


def _setup_project_with_rules(client) -> tuple[int, int]:
    p = client.post("/api/v1/projects", json={"code": "PRJ-CALC", "name": "计算测试"}).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points",
        json={"point_no": "JJC1", "point_type": "settlement"},
    ).json()
    client.put(
        f"/api/v1/projects/{p['id']}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 2},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 4},
            ],
            "source_note": "测试规范",
        },
    )
    return p["id"], pt["id"]


def test_computed_series_endpoint(client):
    _, pt = _setup_project_with_rules(client)
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 4906.0},
    )
    resp = client.get(f"/api/v1/points/{pt}/computed", params={"series_type": "settlement", "component": "value"})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["cumulative_mm"] == 0.0
    assert rows[0]["delta_mm"] is None
    assert rows[1]["delta_mm"] == -1.0
    assert rows[1]["cumulative_mm"] == -1.0
    assert rows[1]["rate_mm_per_day"] == -1.0
    assert rows[1]["warning_level"] == "normal"


def test_warning_status_triggered_by_rules(client):
    """规则生效：累计变化超控制值（速率未超）→ 预警。双控中任一超标即触发。"""
    pid, pt = _setup_project_with_rules(client)
    # 7-30 → 8-10 共 11 天，累计 -37mm，速率 37/11≈3.36mm/d
    # |累计|37 ≥ control(30) 且 < alarm(40) → control；速率 3.36 < alarm(4) 未触发速率报警
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-10", "value_mm": 4870.0},
    )
    resp = client.get(f"/api/v1/projects/{pid}/warning-status")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["point_no"] == "JJC1"
    assert rows[0]["warning_level"] == "control"
    assert rows[0]["cumulative_mm"] == -37.0


def test_warning_status_alarm_by_rate(client):
    """速率超报警值（1 天内大幅变化）→ 报警。"""
    pid, pt = _setup_project_with_rules(client)
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    # 1 天内 -10mm → 速率 10mm/d ≥ alarm(4) → 报警
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 4897.0},
    )
    resp = client.get(f"/api/v1/projects/{pid}/warning-status")
    assert resp.json()[0]["warning_level"] == "alarm"


def test_warning_status_can_be_scoped_to_obs_date(client):
    """按观测日取状态（观测记录页定位用）。

    回归背景：列表页原先固定取"最新一期"，用户在记录页定位到 07-30 时，
    表格里显示的却是 07-31 的报警等级 —— 前后矛盾，等于没定位到。
    """
    pid, pt = _setup_project_with_rules(client)
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 4897.0},
    )

    # 不带日期 → 最新一期 = 07-31 报警
    latest = client.get(f"/api/v1/projects/{pid}/warning-status").json()[0]
    assert latest["obs_date"] == "2026-07-31"
    assert latest["warning_level"] == "alarm"

    # 带日期 → 取该日期的状态
    day1 = client.get(f"/api/v1/projects/{pid}/warning-status", params={"obs_date": "2026-07-30"}).json()[0]
    assert day1["obs_date"] == "2026-07-30"
    assert day1["cumulative_mm"] == 0.0
    assert day1["warning_level"] == "normal"

    day2 = client.get(f"/api/v1/projects/{pid}/warning-status", params={"obs_date": "2026-07-31"}).json()[0]
    assert day2["obs_date"] == "2026-07-31"
    assert day2["warning_level"] == "alarm"


def test_warning_status_on_date_without_data(client):
    """该日无观测 → 按正常返回，且 obs_date 置空（前端据此显示"—"而不是假装有数）。"""
    pid, pt = _setup_project_with_rules(client)
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    row = client.get(f"/api/v1/projects/{pid}/warning-status", params={"obs_date": "2026-08-15"}).json()[0]
    assert row["obs_date"] is None
    assert row["warning_level"] == "normal"


def test_warning_status_rejects_malformed_date(client):
    """日期格式不合法直接 422，不做静默兜底（避免前端拼错日期时看到"一切正常"）。"""
    pid, _ = _setup_project_with_rules(client)
    bad = client.get(f"/api/v1/projects/{pid}/warning-status", params={"obs_date": "2026/07/30"})
    assert bad.status_code == 422
