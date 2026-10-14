# -*- coding: utf-8 -*-
"""测试：测点批量新建（预览不落库 + 落库部分成功）。"""


def _setup(client):
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "批量测试"}).json()
    pid = p["id"]
    # 预置一个已存在测点 JJC1
    client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "both"}).json()
    return pid


def _items(*rows):
    return [dict(r) for r in rows]


def test_batch_preview_returns_three_lists(client):
    """预览：valid / conflict(已存在) / error(格式非法) 三类分离，不落库。"""
    pid = _setup(client)
    resp = client.post(
        f"/api/v1/projects/{pid}/points/batch-preview",
        json={
            "items": [
                {"point_no": "JJC2", "point_type": "settlement"},
                {"point_no": "JJC1", "point_type": "both"},  # 已存在 → conflict
                {"point_no": "坏 编号!", "point_type": "both"},  # 非法 → error
                {"point_no": "JJC3", "point_type": "displacement"},
            ]
        },
    )
    assert resp.status_code == 200
    d = resp.json()
    assert [r["point_no"] for r in d["valid"]] == ["JJC2", "JJC3"]
    assert [r["point_no"] for r in d["conflict"]] == ["JJC1"]
    assert len(d["error"]) == 1
    # 预览不落库：仍只有 1 个点
    assert len(client.get(f"/api/v1/projects/{pid}/points").json()) == 1


def test_batch_preview_dup_in_batch(client):
    """批内重复 → 第二条 conflict。"""
    pid = _setup(client)
    resp = client.post(
        f"/api/v1/projects/{pid}/points/batch-preview",
        json={"items": [{"point_no": "P1", "point_type": "both"}, {"point_no": "P1", "point_type": "both"}]},
    )
    d = resp.json()
    assert len(d["valid"]) == 1
    assert d["conflict"][0]["reason"] == "dup_in_batch"


def test_batch_create_partial_success(client):
    """落库：valid 行创建成功，已存在/非法行跳过或失败，部分成功明细。"""
    pid = _setup(client)
    resp = client.post(
        f"/api/v1/projects/{pid}/points/batch",
        json={
            "items": [
                {"point_no": "JJC2", "point_type": "settlement"},
                {"point_no": "JJC1", "point_type": "both"},  # 已存在 → skipped
                {"point_no": "JJC3", "point_type": "displacement"},
            ]
        },
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["created"] == 2
    assert len(d["skipped"]) == 1
    assert d["skipped"][0]["point_no"] == "JJC1"
    pts = client.get(f"/api/v1/projects/{pid}/points").json()
    assert sorted(p["point_no"] for p in pts) == ["JJC1", "JJC2", "JJC3"]


def test_batch_create_empty(client):
    """空列表 → 正常返回 0。"""
    pid = _setup(client)
    r = client.post(f"/api/v1/projects/{pid}/points/batch", json={"items": []})
    assert r.json()["created"] == 0
