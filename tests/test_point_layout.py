# -*- coding: utf-8 -*-
"""测点平面布置：把各点的初始位置（位移 X/Y 首期读数）摆成一张图。

用户 2026-10-19：「我想看到我每个基坑中初始值在 CAD 中的位置关系」

这份测试钉三件事，每一件错了都会让图变成"看着像对的假图"：
  ① **单位**：库里是 mm（3412987154），图上是 m（3412987.154）—— 少除 1000 就是几千公里；
  ② **离群点要摘出去**：实测九号基坑的 Y 被导进 X 分量（X=510146207），
     不摘的话包围盒被撑到几百公里，整张图缩成一个点；
  ③ **画不上的点必须点名**：纯沉降点没有平面坐标，静默少几个比报错更糟。
"""
from __future__ import annotations

import pytest


# ─────────────────────────── 脚手架 ───────────────────────────


def _mk_project(client, code: str, name: str) -> int:
    return client.post("/api/v1/projects", json={"code": code, "name": name}).json()["id"]


def _mk_point(client, pid: int, point_no: str, point_type: str = "displacement") -> int:
    return client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": point_no, "point_type": point_type},
    ).json()["id"]


def _add_xy(client, point_id: int, obs_date: str, x_mm: float, y_mm: float) -> None:
    for comp, value in (("X", x_mm), ("Y", y_mm)):
        r = client.post(
            f"/api/v1/points/{point_id}/records",
            json={
                "series_type": "displacement",
                "component": comp,
                "obs_date": obs_date,
                "value_mm": value,
            },
        )
        assert r.status_code in (200, 201), r.text


def _layout(client, pid: int) -> dict:
    r = client.get(f"/api/v1/projects/{pid}/points/layout")
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────────────────── 正常路径 ───────────────────────────


def test_coordinates_are_converted_to_metres(client) -> None:
    """库里存 mm，图上必须是 m —— 这是最容易差三个数量级的地方。"""
    pid = _mk_project(client, "PRJ-L1", "布置一号")
    p = _mk_point(client, pid, "JJC1")
    _add_xy(client, p, "2026-07-30", 3412987154, 510224375)

    d = _layout(client, pid)
    assert d["placed_count"] == 1
    pt = d["points"][0]
    assert pt["x_m"] == pytest.approx(3412987.154)
    assert pt["y_m"] == pytest.approx(510224.375)
    assert pt["point_no"] == "JJC1"


def test_bbox_and_spacing_are_real_distances(client) -> None:
    """包围盒和间距得是真距离 —— 用户看的就是"这两个点隔多远"。"""
    pid = _mk_project(client, "PRJ-L2", "布置二号")
    a = _mk_point(client, pid, "JJC1")
    b = _mk_point(client, pid, "JJC2")
    _add_xy(client, a, "2026-07-30", 3412987154, 510224375)
    _add_xy(client, b, "2026-07-30", 3412987661, 510226805)

    d = _layout(client, pid)
    assert d["bbox"] is not None
    assert d["bbox"]["width_m"] == pytest.approx(0.51, abs=0.01)
    assert d["bbox"]["height_m"] == pytest.approx(2.43, abs=0.01)
    # sqrt(0.507² + 2.43²) ≈ 2.482 m
    assert d["segments"][0]["length_m"] == pytest.approx(2.48, abs=0.01)


def test_point_numbers_sort_naturally(client) -> None:
    """JJC2 要排在 JJC10 前面。

    按字符串排会得到 JJC1 → JJC10 → JJC2，连线就横穿整个基坑，
    "相邻间距"全错 —— 而这种错在图上看着还挺"像回事"。
    """
    pid = _mk_project(client, "PRJ-L3", "布置三号")
    for no, x, y in (
        ("JJC1", 3412987154, 510224375),
        ("JJC2", 3412987661, 510226805),
        ("JJC10", 3412991649, 510221797),
    ):
        _add_xy(client, _mk_point(client, pid, no), "2026-07-30", x, y)

    d = _layout(client, pid)
    assert [p["point_no"] for p in d["points"]] == ["JJC1", "JJC2", "JJC10"]
    assert [(s["from"], s["to"]) for s in d["segments"]] == [("JJC1", "JJC2"), ("JJC2", "JJC10")]


def test_takes_the_earliest_observation(client) -> None:
    """位置取**最早一次**，不是最新一次 —— 点会动，初始位置才是布点位置。"""
    pid = _mk_project(client, "PRJ-L4", "布置四号")
    p = _mk_point(client, pid, "JJC1")
    _add_xy(client, p, "2026-08-10", 3412987999, 510224999)  # 后录入，但日期更晚
    _add_xy(client, p, "2026-07-30", 3412987154, 510224375)  # 最早

    d = _layout(client, pid)
    assert d["points"][0]["x_m"] == pytest.approx(3412987.154)


# ─────────────────────────── 画不上的点 ───────────────────────────


def test_settlement_only_points_are_named_not_silently_dropped(client) -> None:
    """纯沉降点没有平面坐标 —— 不画，但必须点名说清原因。"""
    pid = _mk_project(client, "PRJ-L5", "布置五号")
    _mk_point(client, pid, "S1", point_type="settlement")
    _mk_point(client, pid, "S2", point_type="settlement")

    d = _layout(client, pid)
    assert d["placed_count"] == 0 and d["missing_count"] == 2
    assert all("沉降" in m["why"] for m in d["missing"])


def test_half_point_is_reported_as_missing_axis(client) -> None:
    """只有 X 没有 Y —— 画不出来，理由要说清缺哪一轴。"""
    pid = _mk_project(client, "PRJ-L6", "布置六号")
    p = _mk_point(client, pid, "JJC1")
    client.post(
        f"/api/v1/points/{p}/records",
        json={
            "series_type": "displacement",
            "component": "X",
            "obs_date": "2026-07-30",
            "value_mm": 3412987154,
        },
    )

    d = _layout(client, pid)
    assert d["placed_count"] == 0
    assert "缺 Y" in d["missing"][0]["why"]


def test_swapped_xy_is_detected_as_outlier(client) -> None:
    """X/Y 在导入时弄反（Y 值落进 X 列）→ 必须摘出去并说明。"""
    pid = _mk_project(client, "PRJ-L7", "布置七号")
    for no, x, y in (
        ("JJC1", 3412987154, 510224375),
        ("JJC2", 3412987661, 510226805),
        ("JJC3", 3412991649, 510221797),
        ("JJC4", 3412994738, 510219963),
        ("BAD", 510146207, 3413069848),  # 整对反了
    ):
        _add_xy(client, _mk_point(client, pid, no), "2026-07-30", x, y)

    d = _layout(client, pid)
    assert d["placed_count"] == 4, [p["point_no"] for p in d["points"]]
    assert d["missing_count"] == 1
    assert d["missing"][0]["point_no"] == "BAD"
    assert "离群" in d["missing"][0]["why"]

    # 关键：摘掉之后包围盒还是基坑尺度（几十米），不是几百公里
    assert d["bbox"]["width_m"] < 100
    assert d["bbox"]["height_m"] < 100


def test_inactive_records_do_not_move_the_point(client) -> None:
    """作废/被覆盖的读数不能用来定位。

    直接违反的话，用户改错一条读数，点就跑到别的地方去了，
    而且图上不会有任何提示 —— 属于"看着正常、其实错了"的那类。
    """
    pid = _mk_project(client, "PRJ-L8", "布置八号")
    p = _mk_point(client, pid, "JJC1")
    _add_xy(client, p, "2026-07-30", 3412987154, 510224375)

    # 作废走 DELETE /records/{id}（标记 void，不物理删除）
    recs = client.get(f"/api/v1/points/{p}/records").json()
    assert recs, "没读到刚建的记录"
    for r in recs:
        resp = client.delete(f"/api/v1/records/{r['id']}")
        assert resp.status_code in (200, 204), resp.text

    d = _layout(client, pid)
    assert d["placed_count"] == 0, "作废的读数被当成坐标了"
    assert d["missing_count"] == 1


def test_empty_project_returns_empty_layout(client) -> None:
    """没有测点时不报错，返回空结果（界面据此不画图）。"""
    pid = _mk_project(client, "PRJ-L9", "布置九号")
    d = _layout(client, pid)
    assert d["points"] == [] and d["bbox"] is None
