# -*- coding: utf-8 -*-
"""初始值（V0）独立处理测试。

覆盖：首期自动基准、显式初始值优先、无数据不输出、日期表标记（全部日期+初始值日期）。
"""


def _setup(client) -> tuple[int, int]:
    p = client.post("/api/v1/projects", json={"code": "PRJ-BASE", "name": "初始值测试"}).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points", json={"point_no": "JJC1", "point_type": "settlement"}
    ).json()
    return p["id"], pt["id"]


def test_baseline_from_first_observation(client):
    pid, pt = _setup(client)
    for d, v in [("2026-07-30", 4907.0), ("2026-07-31", 4906.0)]:
        client.post(
            f"/api/v1/points/{pt}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
        )
    resp = client.get(f"/api/v1/projects/{pid}/baselines")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    b = rows[0]
    assert b["point_no"] == "JJC1"
    assert b["baseline_value_mm"] == 4907.0  # 首期有效读数
    assert b["baseline_date"] == "2026-07-30"
    assert b["source"] == "first_observation"


def test_baseline_explicit_initial_value_wins(client):
    pid = client.post("/api/v1/projects", json={"code": "PRJ-BASE2", "name": "显式初始值"}).json()["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 4900.0},
    ).json()["id"]
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    b = client.get(f"/api/v1/projects/{pid}/baselines").json()[0]
    assert b["baseline_value_mm"] == 4900.0  # 显式初始值优先
    assert b["source"] == "explicit"


def test_baseline_no_data_returns_empty(client):
    pid, _ = _setup(client)
    rows = client.get(f"/api/v1/projects/{pid}/baselines").json()
    assert rows == []


def test_record_dates_mark_baseline(client):
    pid, pt = _setup(client)
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 4906.0},
    )
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-01", "value_mm": 4880.0},
    )
    data = client.get(f"/api/v1/projects/{pid}/record-dates").json()
    assert data["dates"] == ["2026-07-30", "2026-07-31", "2026-08-01"]
    assert data["baseline_dates"] == ["2026-07-30"]  # 初始值日期单独标记


def test_record_dates_distinguishes_missing_days(client):
    """缺测日（有记录、值全空）要能和"有读数"的日子分开。

    `dates` 与 `dates_with_values` 是给**两个不同页面**用的，别合并：
    · 记录页的"已录入"横条用 `dates` —— 那天确实录过，只是值空着；
    · 导出日报用 `dates_with_values` —— 缺测日导出来是一张空表，
      日历上把它标成"有数据"、还让它可选，用户点进去只会得到空报表。
      （生产库里实测有 4 天是这样，其中一天正好是当天）
    """
    p = client.post("/api/v1/projects", json={"code": "PRJ-MISS", "name": "缺测日"}).json()
    pt = client.post(f"/api/v1/projects/{p['id']}/points",
                     json={"point_no": "JJC1", "point_type": "settlement"}).json()["id"]
    for d, v in (("2026-07-30", 4907.0), ("2026-07-31", 4900.0)):
        client.post(f"/api/v1/points/{pt}/records",
                    json={"series_type": "settlement", "component": "value",
                          "obs_date": d, "value_mm": v})
    # 08-01 缺测：记录在、值为空
    r = client.post(f"/api/v1/points/{pt}/records",
                    json={"series_type": "settlement", "component": "value",
                          "obs_date": "2026-08-01", "value_mm": None})
    assert r.status_code in (200, 201), r.text

    data = client.get(f"/api/v1/projects/{p['id']}/record-dates").json()
    assert data["dates"] == ["2026-07-30", "2026-07-31", "2026-08-01"], "记录页要用含缺测日的全集"
    assert data["dates_with_values"] == ["2026-07-30", "2026-07-31"], (
        f"缺测日混进了「有读数」的日期：{data['dates_with_values']}"
    )


def test_export_page_uses_dates_with_values():
    """源码守卫：导出页必须用 `dates_with_values`，不能用含缺测日的 `dates`。

    否则缺测日会在日历上带横条、还能选中，导出来是一张空表。
    """
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parent.parent
           / "frontend" / "src" / "pages" / "ReportsPage.tsx").read_text(encoding="utf-8")
    assert "dates_with_values" in src, "导出页没有用「有读数的日期」"
    assert "setDates(d.dates_with_values" in src, "导出页又把 dates 当可导出期次用了"


def test_record_dates_project_not_found(client):
    assert client.get("/api/v1/projects/9999/baselines").status_code == 404
    assert client.get("/api/v1/projects/9999/record-dates").status_code == 404


def _rules(client, pid: int) -> None:
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 2},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 4},
            ],
            "source_note": "测试规范",
        },
    )


def _rec(client, pt: int, d: str, v: float) -> None:
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
    )


def test_record_dates_alarm_and_control_are_independent(client):
    """同一天既有报警又有预警时，两个日期集合都要收录。

    回归：早先按「当天最坏等级」只归一类，于是预警日会被报警日吃掉 ——
    日期表图例写着"红=报警日/金=预警日"，用户却查不到那天的预警。
    """
    pid = client.post("/api/v1/projects", json={"code": "PRJ-DATES", "name": "日期标记"}).json()["id"]
    _rules(client, pid)
    pt_a = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "settlement"}).json()["id"]
    pt_b = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC2", "point_type": "settlement"}).json()["id"]

    _rec(client, pt_a, "2026-07-30", 4907.0)
    _rec(client, pt_b, "2026-07-30", 4907.0)
    _rec(client, pt_a, "2026-08-10", 4870.0)  # 累计 -37mm → 预警
    _rec(client, pt_b, "2026-08-10", 4862.0)  # 累计 -45mm → 报警

    data = client.get(f"/api/v1/projects/{pid}/record-dates").json()
    assert data["dates"] == ["2026-07-30", "2026-08-10"]
    assert data["alarm_dates"] == ["2026-08-10"]  # JJC2 报警
    assert data["control_dates"] == ["2026-08-10"]  # JJC1 预警，不被报警吞掉


def test_record_dates_only_normal_days_are_unmarked(client):
    """全正常时不产生任何预警/报警日期标记。"""
    pid, pt = _setup(client)
    _rules(client, pid)
    _rec(client, pt, "2026-07-30", 4907.0)
    _rec(client, pt, "2026-07-31", 4906.8)  # 累计 -0.2mm，远低于控制值
    data = client.get(f"/api/v1/projects/{pid}/record-dates").json()
    assert data["alarm_dates"] == []
    assert data["control_dates"] == []
