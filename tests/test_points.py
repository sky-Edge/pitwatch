# -*- coding: utf-8 -*-
"""Phase 2 测点模块测试。

覆盖：CRUD、项目内 point_no 唯一、跨项目可重名、uuid 内部唯一、删除保护（停用）、404。
"""
import uuid


def _project(client) -> dict:
    resp = client.post("/api/v1/projects", json={"code": "PRJ100", "name": "测试项目"})
    assert resp.status_code == 201
    return resp.json()


def _point_payload(**kwargs) -> dict:
    data = {"point_no": "LZ-01", "point_type": "both", "location_desc": "1# 桩"}
    data.update(kwargs)
    return data


def test_create_point(client):
    p = _project(client)
    resp = client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["point_no"] == "LZ-01"
    assert body["point_type"] == "both"
    assert body["project_id"] == p["id"]
    assert body["status"] == "active"
    # 内部唯一 ID 校验
    assert isinstance(body["id"], int)
    assert uuid.UUID(body["uuid"])  # 合法 UUID
    assert body["initial_value_mm"] is None


def test_point_no_unique_within_project(client):
    p = _project(client)
    client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload())
    resp = client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload(point_no="LZ-01"))
    assert resp.status_code == 409


def test_same_point_no_across_projects_ok(client):
    p1 = _project(client)
    client.post("/api/v1/projects", json={"code": "PRJ200", "name": "项目B"})
    p2 = client.post("/api/v1/projects", json={"code": "PRJ201", "name": "项目C"}).json()

    r1 = client.post(f"/api/v1/projects/{p1['id']}/points", json=_point_payload())
    r2 = client.post(f"/api/v1/projects/{p2['id']}/points", json=_point_payload())
    assert r1.status_code == 201
    assert r2.status_code == 201


def test_create_point_project_not_found(client):
    resp = client.post("/api/v1/projects/9999/points", json=_point_payload())
    assert resp.status_code == 404


def test_list_points_and_filter(client):
    p = _project(client)
    client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload(point_no="LZ-01"))
    client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload(point_no="LZ-02"))

    resp = client.get(f"/api/v1/projects/{p['id']}/points")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_update_point(client):
    p = _project(client)
    pt = client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload()).json()
    resp = client.put(
        f"/api/v1/points/{pt['id']}",
        json={"location_desc": "更新位置", "initial_value_mm": 10.5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["location_desc"] == "更新位置"
    assert body["initial_value_mm"] == 10.5


def test_update_duplicate_point_no_conflict(client):
    p = _project(client)
    a = client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload(point_no="LZ-01")).json()
    client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload(point_no="LZ-02"))
    resp = client.put(f"/api/v1/points/{a['id']}", json={"point_no": "LZ-02"})
    assert resp.status_code == 409


def test_archive_point_keeps_data(client):
    p = _project(client)
    pt = client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload()).json()
    resp = client.delete(f"/api/v1/points/{pt['id']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"

    all_list = client.get(f"/api/v1/projects/{p['id']}/points").json()
    assert len(all_list) == 1

    active_list = client.get(
        f"/api/v1/projects/{p['id']}/points", params={"include_inactive": False}
    ).json()
    assert active_list == []


def test_point_not_found(client):
    assert client.get("/api/v1/points/9999").status_code == 404
    assert client.delete("/api/v1/points/9999").status_code == 404


def test_invalid_point_type(client):
    p = _project(client)
    resp = client.post(f"/api/v1/projects/{p['id']}/points", json=_point_payload(point_type="invalid"))
    assert resp.status_code == 422
