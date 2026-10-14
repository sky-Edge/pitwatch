# -*- coding: utf-8 -*-
"""逐期对比服务测试（经 API 全链路）。

覆盖：相邻对比、首期标记、跨期标记、缺测行、方向语义（沉降/位移、正负号规则）、预警联动。
"""


def _setup(client, point_type="settlement", sign_config=None) -> tuple[int, int]:
    p = client.post(
        "/api/v1/projects",
        json={"code": "PRJ-CMP", "name": "对比测试", "sign_config": sign_config or {}},
    ).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points",
        json={"point_no": "JJC1", "point_type": point_type},
    ).json()
    client.put(
        f"/api/v1/projects/{p['id']}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
            ],
            "source_note": "对比测试规则",
        },
    )
    return p["id"], pt["id"]


def _add(client, pt, dates_vals):
    for d, v in dates_vals:
        r = client.post(
            f"/api/v1/points/{pt}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
        )
        assert r.status_code == 201, r.text


def test_comparison_first_and_adjacent(client):
    pid, pt = _setup(client)
    _add(client, pt, [("2026-07-30", 4907.0), ("2026-07-31", 4906.0), ("2026-08-01", 4880.0)])

    resp = client.get(f"/api/v1/projects/{pid}/comparison")
    assert resp.status_code == 200
    series = resp.json()[0]
    assert series["point_no"] == "JJC1"
    assert series["component"] == "value"
    periods = series["periods"]
    assert len(periods) == 3

    # 首期：无基准、ΔV=None、C=0
    assert periods[0]["compare_kind"] == "first"
    assert periods[0]["compare_to_date"] is None
    assert periods[0]["delta_mm"] is None
    assert periods[0]["cumulative_mm"] == 0.0
    assert periods[0]["direction_label"] == "无变化"

    # 第二期：紧邻上期
    assert periods[1]["compare_kind"] == "adjacent"
    assert periods[1]["compare_to_date"] == "2026-07-30"
    assert periods[1]["delta_mm"] == -1.0
    assert periods[1]["prev_value_mm"] == 4907.0

    # 第三期：紧邻上期，ΔV=4880-4906=-26
    assert periods[2]["compare_kind"] == "adjacent"
    assert periods[2]["delta_mm"] == -26.0
    assert periods[2]["cumulative_mm"] == -27.0


def test_comparison_skip_over_missing(client):
    """中间缺测 → 与最近有效期比，标记 skip 与天数。"""
    pid, pt = _setup(client)
    _add(client, pt, [("2026-07-30", 4907.0), ("2026-08-01", 4880.0)])  # 7-31 缺测

    resp = client.get(f"/api/v1/projects/{pid}/comparison")
    periods = resp.json()[0]["periods"]
    assert periods[1]["compare_kind"] == "skip"
    assert periods[1]["compare_to_date"] == "2026-07-30"
    assert periods[1]["skipped_days"] == 2
    assert periods[1]["delta_mm"] == -27.0


def test_comparison_missing_row(client):
    """缺测期：独立一行，值全空，标记 direction_label=缺测。"""
    pid, pt = _setup(client)
    _add(client, pt, [("2026-07-30", 4907.0), ("2026-07-31", None)])

    resp = client.get(f"/api/v1/projects/{pid}/comparison")
    periods = resp.json()[0]["periods"]
    assert len(periods) == 2
    assert periods[1]["compare_kind"] == "missing"
    assert periods[1]["value_mm"] is None
    assert periods[1]["delta_mm"] is None
    assert periods[1]["direction_label"] == "缺测"


def test_direction_settlement_default_down(client):
    """沉降默认：正值=下沉。ΔV<0 → 隆起。"""
    pid, pt = _setup(client)
    _add(client, pt, [("2026-07-30", 4907.0), ("2026-07-31", 4906.0)])
    periods = client.get(f"/api/v1/projects/{pid}/comparison").json()[0]["periods"]
    assert periods[1]["direction"] == "up"
    assert periods[1]["direction_label"] == "隆起"


def test_direction_settlement_custom_positive_up(client):
    """自定义 sign_config：沉降正值=上升 → ΔV<0 → 下沉。"""
    pid, pt = _setup(client, sign_config={"settlement": {"positive_means": "up"}})
    _add(client, pt, [("2026-07-30", 4907.0), ("2026-07-31", 4906.0)])
    periods = client.get(f"/api/v1/projects/{pid}/comparison").json()[0]["periods"]
    assert periods[1]["direction"] == "down"
    assert periods[1]["direction_label"] == "下沉"


def test_direction_displacement_toward_pit(client):
    """位移默认：正值=向坑内。ΔV>0 → 向基坑内。"""
    pid = client.post("/api/v1/projects", json={"code": "PRJ-CMP2", "name": "位移测试"}).json()["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points", json={"point_no": "WY1", "point_type": "displacement"}
    ).json()["id"]
    for d, v in [("2026-07-30", 3413055112.0), ("2026-07-31", 3413055113.0)]:
        client.post(
            f"/api/v1/points/{pt}/records",
            json={"series_type": "displacement", "component": "X", "obs_date": d, "value_mm": v},
        )
    periods = client.get(f"/api/v1/projects/{pid}/comparison").json()[0]["periods"]
    assert periods[1]["direction"] == "toward_pit"
    assert periods[1]["direction_label"] == "向基坑内"


def test_comparison_warning_linked(client):
    """对比结果携带预警等级（累计超控制值 → control）。"""
    pid, pt = _setup(client)
    _add(client, pt, [("2026-07-30", 4907.0), ("2026-08-10", 4870.0)])  # 累计 -37 ≥ 30
    periods = client.get(f"/api/v1/projects/{pid}/comparison").json()[0]["periods"]
    assert periods[1]["warning_level"] == "control"
    assert periods[1]["triggers"]


def test_comparison_period_global_index(client):
    """期次按项目全局观测日序号编号：首个观测日=0（初始值），其后 1/2/3…
    （与母版/内置日报导出的期次口径一致——首测不叫第一期而叫初始值。）"""
    pid, pt = _setup(client)
    _add(client, pt, [("2026-07-30", 4907.0), ("2026-07-31", 4906.0), ("2026-08-01", 4880.0)])

    resp = client.get(f"/api/v1/projects/{pid}/comparison")
    assert resp.status_code == 200
    periods = resp.json()[0]["periods"]
    # 全局只有这 3 个观测日：07-30=初始值(0)、07-31=第一期(1)、08-01=第二期(2)
    assert [p["period"] for p in periods] == [0, 1, 2]
    assert periods[0]["obs_date"] == "2026-07-30"
    assert periods[1]["obs_date"] == "2026-07-31"
