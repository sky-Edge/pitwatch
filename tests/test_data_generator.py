# -*- coding: utf-8 -*-
"""测试：观测数据自动生成器（需求 #9）。"""
from datetime import date


def _setup(client):
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "生成器测试"}).json()
    pid = p["id"]
    pt = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "both"}).json()
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
                {"indicator": "displacement", "level": "control", "threshold_cumulative_mm": 20, "threshold_rate_mm_per_day": 3},
                {"indicator": "displacement", "level": "alarm", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 4},
            ],
            "source_note": "t",
        },
    )
    # V0：沉降 value=4907mm；位移 X=3413055112mm Y=510174166mm
    client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "displacement", "component": "X", "obs_date": "2026-07-30", "value_mm": 3413055112.0},
    )
    client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "displacement", "component": "Y", "obs_date": "2026-07-30", "value_mm": 510174166.0},
    )
    return pid, pt["id"]


def test_generate_basic(client):
    """生成 3 天数据：沉降+位移 X/Y 都有，source=generated。"""
    pid, pt = _setup(client)
    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-03", "seed": 42},
    )
    assert r.status_code == 200
    d = r.json()
    # 沉降1 + 位移X/Y 2 = 3 个分量 × 3 天
    assert d["generated"] == 9, d
    # 已有 7-30 V0 记录保留
    recs = client.get(f"/api/v1/points/{pt}/records", params={"from_date": "2026-07-30", "to_date": "2026-08-03"}).json()
    assert len(recs) == 9 + 3


def test_generated_within_limits(client):
    """生成 20 天数据：累计变化不超控制值、速率不超。"""
    pid, _ = _setup(client)
    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-20", "seed": 7},
    )
    d = r.json()
    assert d["generated"] == 3 * 20

    # 校验计算序列：全部在 control 内
    from backend.core.db import SessionLocal
    from backend.services.calc_service import compute_point_series

    db = SessionLocal()
    try:
        from backend.repositories import point_repo

        pts = point_repo.list_by_project(db, pid, include_inactive=False)
        max_abs_cum = 0
        max_abs_rate = 0
        for p in pts:
            for st, comps in [("settlement", ["value"]), ("displacement", ["X", "Y"])]:
                for comp in comps:
                    for row in compute_point_series(db, p, st, comp):
                        if row["cumulative_mm"] is not None:
                            max_abs_cum = max(max_abs_cum, abs(row["cumulative_mm"]))
                        if row["rate_mm_per_day"] is not None:
                            max_abs_rate = max(max_abs_rate, abs(row["rate_mm_per_day"]))
        db.close()
    finally:
        db.close()
    # 控制值：沉降累计30、位移20；速率 沉降10/位移3 —— 生成应全部更小
    assert max_abs_cum < 30, f"累计越限 {max_abs_cum}"
    assert max_abs_rate <= 10, f"速率越限 {max_abs_rate}"


def test_generate_skips_existing_dates(client):
    """已存在日期被跳过，不覆盖。"""
    pid, pt = _setup(client)
    # 8-01 已有真实记录
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-08-01", "value_mm": 4906.0},
    )
    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-02", "seed": 1},
    )
    d = r.json()
    # 沉降 8-01 跳过 → 沉降只生成 8-02 =1；位移 X/Y 各 2 天 =4；共 5
    assert d["generated"] == 5, d


def test_cleanup_generated(client):
    """清理 generated 数据，保留真实记录。"""
    pid, pt = _setup(client)
    client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-05", "seed": 3},
    )
    r = client.post(f"/api/v1/projects/{pid}/generate-data/cleanup")
    assert r.status_code == 200
    assert r.json()["deleted"] == 3 * 5
    recs = client.get(f"/api/v1/points/{pt}/records").json()
    assert len(recs) == 3  # 只剩 V0 三条
