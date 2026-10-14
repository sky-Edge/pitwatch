# -*- coding: utf-8 -*-
"""Phase 1 项目模块测试。

覆盖：CRUD、唯一性约束、sign_config JSON、删除保护（归档）、404/422。
"""
from backend.schemas.project import ProjectCreate


def _payload(code: str = "PRJ001", name: str = "嘉善兴善公路基坑监测", **kwargs) -> dict:
    data = {"code": code, "name": name}
    data.update(kwargs)
    return data


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_project(client):
    resp = client.post("/api/v1/projects", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "PRJ001"
    assert body["name"] == "嘉善兴善公路基坑监测"
    assert body["is_active"] is True
    assert body["sign_config"] == {}


def test_create_project_with_sign_config(client):
    sign_config = {
        "settlement": {"positive_means": "down", "desc": "正值=下沉，负值=隆起"},
        "displacement": {"positive_means": "toward_pit", "desc": "正值=向基坑内位移"},
    }
    resp = client.post("/api/v1/projects", json=_payload(code="PRJ002", sign_config=sign_config))
    assert resp.status_code == 201
    assert resp.json()["sign_config"]["settlement"]["positive_means"] == "down"


def test_duplicate_code_conflict(client):
    client.post("/api/v1/projects", json=_payload())
    resp = client.post("/api/v1/projects", json=_payload(name="另一个项目"))
    assert resp.status_code == 409


def test_list_projects(client):
    client.post("/api/v1/projects", json=_payload())
    client.post("/api/v1/projects", json=_payload(code="PRJ002", name="项目B"))
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_project_not_found(client):
    resp = client.get("/api/v1/projects/9999")
    assert resp.status_code == 404


def test_update_project(client):
    created = client.post("/api/v1/projects", json=_payload()).json()
    resp = client.put(
        f"/api/v1/projects/{created['id']}",
        json={"name": "更新后的名称", "location": "上海浦东"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "更新后的名称"
    assert body["location"] == "上海浦东"


def test_update_duplicate_code_conflict(client):
    client.post("/api/v1/projects", json=_payload())
    p2 = client.post("/api/v1/projects", json=_payload(code="PRJ002", name="项目B")).json()
    resp = client.put(f"/api/v1/projects/{p2['id']}", json={"code": "PRJ001"})
    assert resp.status_code == 409


def test_archive_project_keeps_data(client):
    created = client.post("/api/v1/projects", json=_payload()).json()
    resp = client.delete(f"/api/v1/projects/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # 归档后仍可通过 include_inactive=true 查到
    all_list = client.get("/api/v1/projects", params={"include_inactive": True}).json()
    assert any(p["id"] == created["id"] for p in all_list)

    # include_inactive=false 时隐藏
    active_list = client.get("/api/v1/projects", params={"include_inactive": False}).json()
    assert all(p["is_active"] for p in active_list)


def test_archive_then_reactivate_project(client):
    created = client.post("/api/v1/projects", json=_payload()).json()

    archived = client.delete(f"/api/v1/projects/{created['id']}")
    assert archived.status_code == 200
    assert archived.json()["is_active"] is False

    reactivated = client.put(f"/api/v1/projects/{created['id']}", json={"is_active": True})
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_validation_error(client):
    resp = client.post("/api/v1/projects", json={"code": "", "name": ""})
    assert resp.status_code == 422
