# -*- coding: utf-8 -*-
"""Chart Data Builder 测试。"""
import pytest


def _setup(client):
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "测试基坑"}).json()
    pid = p["id"]
    j1 = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "both"},
    ).json()
    j2 = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC2", "point_type": "both"},
    ).json()
    # 沉降序列：JJC1 两期、JJC2 一期
    for pid_pt, vals in [(j1["id"], [(4907.0, "2026-07-30"), (4880.0, "2026-07-31")]), (j2["id"], [(5200.0, "2026-07-30")])]:
        for v, d in vals:
            client.post(
                f"/api/v1/points/{pid_pt}/records",
                json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
            )
    return pid


def test_chart_data_settlement(client):
    """沉降曲线：返回全部测点序列 + 日期轴。"""
    pid = _setup(client)
    resp = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"})
    assert resp.status_code == 200
    d = resp.json()
    assert d["indicator"] == "settlement"
    assert d["component"] == "value"
    assert d["dates"] == ["2026-07-30", "2026-07-31"]
    assert len(d["series"]) == 2
    j1 = next(s for s in d["series"] if s["point_no"] == "JJC1")
    # 只钉"曲线画什么"的两个字段。2026-10-19 起每个点还带 record_id / value_mm（拖拽调整要用），
    # 写死整个 dict 的话，以后再加字段这条测试就会无谓地红。
    assert [(p["date"], p["cumulative_mm"]) for p in j1["data"]] == [
        ("2026-07-30", 0.0),
        ("2026-07-31", -27.0),
    ]
    # 但这两个字段是拖拽的前提：缺了前端点了没反应，必须钉住
    assert all(p["record_id"] and p["value_mm"] is not None for p in j1["data"])


def test_chart_data_displacement_component(client):
    """位移曲线：指定分量（X/Y）。"""
    pid = _setup(client)
    j1 = client.get(f"/api/v1/projects/{pid}/points").json()[0]
    client.post(
        f"/api/v1/points/{j1['id']}/records",
        json={"series_type": "displacement", "component": "X", "obs_date": "2026-07-30", "value_mm": 3413055112.0},
    )
    client.post(
        f"/api/v1/points/{j1['id']}/records",
        json={"series_type": "displacement", "component": "Y", "obs_date": "2026-07-30", "value_mm": 510174166.0},
    )
    resp = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "displacement", "component": "Y"})
    d = resp.json()
    assert d["component"] == "Y"
    y_series = [s for s in d["series"] if s["component"] == "Y" and s["point_no"] == "JJC1"]
    assert len(y_series) == 1
    assert y_series[0]["data"][0]["cumulative_mm"] == 0.0


def test_chart_data_warning_points(client):
    """预警叠加点：超限期被标记。"""
    pid = _setup(client)
    # 配置八号模板规则（沉降 control=30 / alarm=40）
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
    # JJC1 再录一期跨 6 天：4880 → 4865，累计 -42mm 超报警40，但速率 -42/6=-7mm/d 未超 10
    j1 = client.get(f"/api/v1/projects/{pid}/points").json()[0]
    client.post(
        f"/api/v1/points/{j1['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-06", "value_mm": 4865.0},
    )
    resp = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"})
    d = resp.json()
    wps = d["warning_points"]
    # 7-31 单日 -27mm → 速率 -27mm/d 超报警速率12 → alarm；8-06 累计-42 → alarm
    assert len(wps) == 2
    assert {w["warning_level"] for w in wps} == {"alarm"}
    alarm = next(w for w in wps if w["date"] == "2026-08-06")
    assert alarm["point_no"] == "JJC1"
    assert alarm["cumulative_mm"] == -42.0  # 累计 -42mm ≥ 报警40（速率 -7 未超，纯累计触发）


def test_chart_data_missing_skipped(client):
    """缺测期不落线（曲线不断点）。"""
    pid = _setup(client)
    j1 = client.get(f"/api/v1/projects/{pid}/points").json()[0]
    client.post(
        f"/api/v1/points/{j1['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-01", "value_mm": None},
    )
    resp = client.get(f"/api/v1/projects/{pid}/chart-data", params={"indicator": "settlement"})
    d = resp.json()
    j1s = next(s for s in d["series"] if s["point_no"] == "JJC1")
    dates = [x["date"] for x in j1s["data"]]
    assert "2026-08-01" not in dates  # 缺测期被跳过
