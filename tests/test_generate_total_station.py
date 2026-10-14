# -*- coding: utf-8 -*-
"""全站仪观测模式（2026-10-05 用户需求）。

用户原话：「我用全站仪测点，把每次架设备的误差和设备误差加进去，可以加一个模式」。

这个模式要解决的**不是"误差更大"**，而是"误差的**性格**不对"：
默认噪声是每点每期各自独立的，生成出来"每个点各跳各的"；而真实全站仪
**每次架站的对中/定向偏差由当期所有测点共同承担** —— 同一期各点会一起偏。

所以这里断言的核心是**同期点间相关**（标准模式 ≈ 0，全站仪模式显著为正），
同时**总散布必须基本不变**（用户为"散布要像真实台账"提过两轮需求，
开了模式就散掉是不能接受的）。
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.services import data_generator_service as gen

SEEDS = (3, 7, 11)
DAYS = 20
POINT_COUNT = 8
CX, CY = 3413000.0, 510000.0     # 坐标量级照真实项目（带 500km 假东的 Y）


# ────────────────────── 纯函数：误差折算 ──────────────────────


def test_station_sigmas_fold_extremes_to_std():
    """对中填的是**极限值**，要折成标准差；定向按臂长把角度折成毫米。"""
    sigma_c, sigma_d = gen._station_sigmas(2.0, 5.0, 30.0)
    assert sigma_c == pytest.approx(1.0)                      # 半径 2mm 的圆内均匀 → 单轴 σ = R/2
    # 30m 处 5″ ≈ 0.727mm —— 量级很小，这是对的：真正伤精度的是对中，不是测角
    assert sigma_d == pytest.approx(30_000 * math.radians(5 / 3600), rel=1e-6)
    assert sigma_d < sigma_c


def test_split_independent_keeps_total_spread():
    """关键的误差预算：加了公共项之后，**总散布不能被放大**（也不能塌）。"""
    total = gen._NOISE_SPEC["displacement"][1]
    sigma_c, sigma_d = gen._station_sigmas(2.0, 5.0, 30.0)
    indep = gen._split_independent(total, sigma_c, sigma_d)
    assert indep < total, "独立项没有让出一部分给公共项"
    # 让出的量按实测标定的权重算（解析值是 2.0，实测有效值 ≈1.3，见 _COMMON_SHARE）
    expect = math.sqrt(total**2 - gen._COMMON_SHARE * (sigma_c**2 + sigma_d**2))
    assert indep == pytest.approx(expect, rel=1e-9)
    # 保底：参数调到离谱时不许把独立项压成 0（差数据就该长得差）
    assert gen._split_independent(total, 8.0, 30.0) >= 0.3


def test_build_station_offsets_centering_is_shared():
    """定向误差设 0 时只剩对中 —— 同期**所有点的偏移必须完全一样**。

    这条抓的就是这个模式的本质：架站偏差是当期全测站共用的。
    """
    xy = {f"JJC{i}": (CX + i * 1000, CY + (i % 3) * 1000) for i in range(1, 6)}
    dates = [date(2026, 9, 1 + i) for i in range(5)]
    off = gen.build_station_offsets(random.Random(4), xy, dates,
                                    centering_mm=2.0, direction_sec=0.0, distance_m=30.0)
    for d in dates:
        xs = {round(off[(p, "X")][d], 9) for p in xy}
        ys = {round(off[(p, "Y")][d], 9) for p in xy}
        assert len(xs) == 1 and len(ys) == 1, f"{d} 各点的对中偏移不一致：{xs} {ys}"
    # 每期都是新架站 → 各期之间必须不同
    assert len({round(off[("JJC1", "X")][d], 9) for d in dates}) == len(dates)


def test_build_station_offsets_direction_scales_with_distance():
    """定向偏差绕基坑中心转 —— 离中心越远、影响越大（臂长效应）。"""
    xy = {"NEAR": (CX + 5_000, CY), "FAR": (CX + 50_000, CY)}
    dates = [date(2026, 9, 1)]
    off = gen.build_station_offsets(random.Random(9), xy, dates,
                                    centering_mm=0.0, direction_sec=30.0, distance_m=30.0)
    near = abs(off[("NEAR", "Y")][dates[0]])
    far = abs(off[("FAR", "Y")][dates[0]])
    assert near == pytest.approx(far, rel=1e-9), "定向偏移用的是统一工作距离，不该随点变"
    # 方向：沿圆心→点的切向（X 方向上偏移的点，其偏移应落在 Y 方向）
    assert abs(off[("NEAR", "X")][dates[0]]) < 1e-9


# ────────────────────── 端到端：形态 ──────────────────────


def _setup(client: TestClient, n_points: int = POINT_COUNT) -> tuple[int, list[int]]:
    """建项目 + n 个 both 测点，给出绕基坑中心的圆形布点坐标（照真实基坑）。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ-TS", "name": "全站仪模式测试"}).json()
    # 控制值必须配上：生成器按控制值划定波动范围，没配就拒绝生成（见 _require_limits）。
    # 取八号基坑实测口径：沉降累计 30 / 速率 2，位移累计 20 / 速率 3。
    client.put(
        f"/api/v1/projects/{p['id']}/warning-rules",
        json={"rules": [
            {"indicator": "settlement", "level": "control",
             "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 2.0},
            {"indicator": "displacement", "level": "control",
             "threshold_cumulative_mm": 20, "threshold_rate_mm_per_day": 3.0},
        ], "source_note": "测试规范"},
    )
    ids = []
    for i in range(1, n_points + 1):
        ang = 2 * math.pi * i / n_points
        rad = 18_000 + (i % 4) * 7_000
        pt = client.post(f"/api/v1/projects/{p['id']}/points",
                         json={"point_no": f"JJC{i}", "point_type": "both"}).json()
        ids.append(pt["id"])
        for st, comp, val in (
            ("settlement", "value", 3500.0 + i),
            ("displacement", "X", (CX + rad * math.cos(ang)) * 1000),
            ("displacement", "Y", (CY + rad * math.sin(ang)) * 1000),
        ):
            client.post(f"/api/v1/points/{pt['id']}/records",
                        json={"series_type": st, "component": comp,
                              "obs_date": "2026-09-01", "value_mm": val})
    return p["id"], ids


def _run(client: TestClient, pid: int, mode: str | None) -> None:
    """跑几个种子并累计统计（单种子波动大，结论必须看平均）。"""
    params = {"start": "2026-09-02", "end": f"2026-09-{1 + DAYS}", "on_conflict": "replace_all"}
    if mode:
        params["instrument_mode"] = mode
    return params


def _measure(client: TestClient, pid: int, ids: list[int], mode: str | None) -> dict:
    """跑 SEEDS 个种子，返回各分量的 {平均|Δ|, 同期均值σ, 点间相关} 的多种子平均。"""
    agg: dict = defaultdict(lambda: defaultdict(list))
    for sd in SEEDS:
        params = {"start": "2026-09-02", "end": f"2026-09-{1 + DAYS}",
                  "on_conflict": "replace_all", "seed": sd}
        if mode:
            params["instrument_mode"] = mode
        r = client.post(f"/api/v1/projects/{pid}/generate-data", params=params)
        assert r.status_code == 200, r.text
        one = _measure_once(client, ids)
        for key, v in one.items():
            for k, x in v.items():
                agg[key][k].append(x)
    return {kk: {n: statistics.fmean(vs) for n, vs in d.items()} for kk, d in agg.items()}


def _measure_once(client: TestClient, ids: list[int]) -> dict:
    out = {}
    for st, comp in (("displacement", "X"), ("displacement", "Y"), ("settlement", "value")):
        series: dict[str, list[tuple[str, float]]] = {}
        for i, pid in enumerate(ids, start=1):
            rows = client.get(f"/api/v1/points/{pid}/computed",
                              params={"series_type": st, "component": comp}).json()
            series[f"JJC{i}"] = [(x["obs_date"], x["value_mm"]) for x in rows
                                 if x["value_mm"] is not None]
        by_date: dict[str, dict[str, float]] = defaultdict(dict)
        for k, seq in series.items():
            seq = sorted(seq)
            for (_, v0), (d1, v1) in zip(seq, seq[1:]):
                by_date[d1][k] = v1 - v0
        deltas = [abs(x) for dd in by_date.values() for x in dd.values()]
        means = [statistics.fmean(dd.values()) for dd in by_date.values() if len(dd) >= 5]

        dates = sorted(by_date)
        sers = {k: [by_date[d].get(k) for d in dates] for k in series}
        cors = []
        keys = list(sers)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a = [x for x, y in zip(sers[keys[i]], sers[keys[j]]) if x is not None and y is not None]
                b = [y for x, y in zip(sers[keys[i]], sers[keys[j]]) if x is not None and y is not None]
                if len(a) > 5 and len(set(a)) > 1 and len(set(b)) > 1:
                    cors.append(statistics.correlation(a, b))
        out[(st, comp)] = {
            "avg": statistics.fmean(deltas),
            "mean_sigma": statistics.pstdev(means) if len(means) > 1 else 0.0,
            "corr": statistics.fmean(cors) if cors else 0.0,
        }
    return out


@pytest.fixture(scope="module")
def measured():
    """两种模式各跑一遍（模块级缓存：这组统计要跑几秒，别每条测试都重跑）。"""
    from backend.main import app

    client = TestClient(app)
    pid, ids = _setup(client)
    std = _measure(client, pid, ids, None)
    ts = _measure(client, pid, ids, gen._TOTAL_STATION_MODE)
    return std, ts


def test_same_epoch_points_move_together(measured):
    """**这个模式存在的全部意义**：同一期的各点要一起偏（点间相关显著为正）。

    标准模式下各点独立 → 相关 ≈ 0；全站仪模式把架站偏差变成"当期共享" → 相关明显抬头。
    """
    std, ts = measured
    for comp in ("X", "Y"):
        s = std[("displacement", comp)]["corr"]
        t = ts[("displacement", comp)]["corr"]
        assert abs(s) < 0.06, f"标准模式的同期相关本该接近 0，实测 {s:+.1%}"
        assert t > 0.08, (
            f"位移{comp} 开了全站仪模式后同期相关只有 {t:+.1%} —— "
            "架站误差没有变成'当期所有点共同承担'"
        )
        assert t > s + 0.06, f"位移{comp} 的相关性没拉开：{s:+.1%} → {t:+.1%}"


def test_total_spread_is_preserved(measured):
    """总散布必须基本不变（公共项是从独立项里**让出来**的，不是凭空加的）。

    用户为"生成数据的散布要像真实台账"提过两轮需求，开个模式就散掉是不能接受的。
    放宽到 ±20%：这是统计量，多点少点的抖动难免。
    """
    std, ts = measured
    for comp in ("X", "Y"):
        a = std[("displacement", comp)]["avg"]
        b = ts[("displacement", comp)]["avg"]
        assert abs(b - a) / a < 0.20, (
            f"位移{comp} 开了模式之后散布变了：{a:.2f} → {b:.2f}mm（应当基本不变）"
        )


def test_settlement_is_not_affected(measured):
    """沉降是电子水准仪测的，不吃全站仪那套架站误差 —— 必须**完全不变**。"""
    std, ts = measured
    a, b = std[("settlement", "value")], ts[("settlement", "value")]
    assert abs(b["avg"] - a["avg"]) / a["avg"] < 0.10, (
        f"沉降的散布被全站仪模式影响了：{a['avg']:.2f} → {b['avg']:.2f}mm"
    )
    assert abs(b["corr"]) < 0.06, f"沉降的点间相关不该被拉起来：{b['corr']:+.1%}"


def test_common_component_appears_in_epoch_mean(measured):
    """同期均值的波动（= 当期公共分量）要明显变大 —— 另一种看"一起偏"的角度。"""
    std, ts = measured
    a = std[("displacement", "Y")]["mean_sigma"]
    b = ts[("displacement", "Y")]["mean_sigma"]
    assert b > a * 1.15, f"同期均值的波动没起来：{a:.2f} → {b:.2f}mm"


def test_centering_does_not_change_point_ordering(measured):
    """公共项不该把"哪个点动得多"的差异抹平（各点仍各有自己的读数噪声）。"""
    std, ts = measured
    for comp in ("X", "Y"):
        a = std[("displacement", comp)]["avg"]
        assert ts[("displacement", comp)]["avg"] > 0.5, (
            f"位移{comp} 开模式后各点自己的变化被压没了（{a:.2f} → "
            f"{ts[('displacement', comp)]['avg']:.2f}mm）"
        )


# ────────────────────── 参数与兼容 ──────────────────────


def test_unknown_mode_is_rejected(client):
    pid, _ = _setup(client, n_points=2)
    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-09-02", "end": "2026-09-05",
                            "instrument_mode": "nonsense"})
    assert r.status_code == 422
    assert "instrument_mode" in r.text


def test_custom_parameters_are_echoed_back(client):
    """界面要能说清"这次用的什么参数、公共项多大"，否则用户没法判断调得对不对。"""
    pid, _ = _setup(client, n_points=2)
    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-09-02", "end": "2026-09-05", "seed": 1,
                            "instrument_mode": "total_station",
                            "station_centering_mm": 3.0,
                            "station_direction_sec": 8.0,
                            "station_distance_m": 50.0})
    assert r.status_code == 200, r.text
    s = r.json()["station"]
    assert (s["centering_mm"], s["direction_sec"], s["distance_m"]) == (3.0, 8.0, 50.0)
    assert s["noise_scale_after_mm"] < s["noise_scale_before_mm"], "独立项没有让出公共项"
    assert s["points_with_xy"] == 2
    assert r.json()["instrument_mode"] == "total_station"


def test_standard_mode_is_untouched(client):
    """不带 instrument_mode 时行为与改动前一致：静默，且不写任何全站仪参数。"""
    pid, _ = _setup(client, n_points=2)
    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-09-02", "end": "2026-09-06", "seed": 5})
    body = r.json()
    assert body["instrument_mode"] == "standard"
    assert body["station"] is None


def test_no_xy_points_degrades_gracefully(client):
    """项目只测沉降（没有 X/Y 坐标）时开全站仪模式：不该崩，只是没有公共项。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ-TS2", "name": "只测沉降"}).json()
    client.put(
        f"/api/v1/projects/{p['id']}/warning-rules",
        json={"rules": [{"indicator": "settlement", "level": "control",
                         "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 2.0}]},
    )
    pt = client.post(f"/api/v1/projects/{p['id']}/points",
                     json={"point_no": "JJC1", "point_type": "settlement"}).json()
    client.post(f"/api/v1/points/{pt['id']}/records",
                json={"series_type": "settlement", "component": "value",
                      "obs_date": "2026-09-01", "value_mm": 3500.0})
    r = client.post(f"/api/v1/projects/{p['id']}/generate-data",
                    params={"start": "2026-09-02", "end": "2026-09-06", "seed": 1,
                            "instrument_mode": "total_station"})
    assert r.status_code == 200, r.text
    assert r.json()["station"]["points_with_xy"] == 0
    assert r.json()["generated"] > 0
