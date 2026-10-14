# -*- coding: utf-8 -*-
"""Phase 3 观测记录模块测试。

覆盖：录入（含缺测）、series_type/component 校验、重复录入 409、
位移 X/Y 双分量、列表过滤、改备注/状态、作废不删除、禁止改数值。
"""


def _project_with_points(client, point_type: str = "both") -> tuple[dict, dict]:
    p = client.post("/api/v1/projects", json={"code": "PRJ-OBS", "name": "观测测试项目"}).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points",
        json={"point_no": "JJC1", "point_type": point_type, "location_desc": "1#桩"},
    ).json()
    return p, pt


def test_create_settlement_record(client):
    _, pt = _project_with_points(client)
    resp = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["value_mm"] == 4907.0
    assert body["status"] == "normal"
    assert body["source"] == "manual"


def test_create_missing_record_sets_status_missing(client):
    _, pt = _project_with_points(client)
    resp = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30"},
    )
    assert resp.status_code == 201
    assert resp.json()["value_mm"] is None
    assert resp.json()["status"] == "missing"


def test_duplicate_record_conflict(client):
    _, pt = _project_with_points(client)
    payload = {"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0}
    assert client.post(f"/api/v1/points/{pt['id']}/records", json=payload).status_code == 201
    resp = client.post(f"/api/v1/points/{pt['id']}/records", json=payload)
    assert resp.status_code == 409


def test_series_must_match_point_type(client):
    # settlement 类型测点不允许录位移
    _, pt = _project_with_points(client, point_type="settlement")
    resp = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "displacement", "component": "X", "obs_date": "2026-07-30", "value_mm": 100.0},
    )
    assert resp.status_code == 422


def test_component_combination_validation(client):
    # 沉降不允许 X 分量
    _, pt = _project_with_points(client)
    resp = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "X", "obs_date": "2026-07-30", "value_mm": 100.0},
    )
    assert resp.status_code == 422


def test_displacement_xy_components(client):
    _, pt = _project_with_points(client, point_type="both")
    for comp in ("X", "Y"):
        resp = client.post(
            f"/api/v1/points/{pt['id']}/records",
            json={"series_type": "displacement", "component": comp, "obs_date": "2026-07-30", "value_mm": 3413055.0},
        )
        assert resp.status_code == 201
    records = client.get(f"/api/v1/points/{pt['id']}/records").json()
    comps = {r["component"] for r in records}
    assert comps == {"X", "Y"}


def test_list_filter_by_series_and_date(client):
    _, pt = _project_with_points(client)
    for d in ("2026-07-30", "2026-07-31"):
        client.post(
            f"/api/v1/points/{pt['id']}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": 4907.0},
        )
    all_records = client.get(f"/api/v1/points/{pt['id']}/records").json()
    assert len(all_records) == 2

    filtered = client.get(
        f"/api/v1/points/{pt['id']}/records", params={"from_date": "2026-07-31"}
    ).json()
    assert len(filtered) == 1


def test_update_remark_and_status(client):
    _, pt = _project_with_points(client)
    rec = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    ).json()
    resp = client.put(f"/api/v1/records/{rec['id']}", json={"remark": "复核中", "status": "suspect"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["remark"] == "复核中"
    assert body["status"] == "suspect"


def test_cannot_modify_value(client):
    _, pt = _project_with_points(client)
    rec = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    ).json()
    # 原始数据不可改数值：传 value_mm 应被拒绝（extra=forbid → 422）
    resp = client.put(f"/api/v1/records/{rec['id']}", json={"value_mm": 999.0})
    assert resp.status_code == 422


def test_void_record_keeps_data(client):
    _, pt = _project_with_points(client)
    rec = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    ).json()
    resp = client.delete(f"/api/v1/records/{rec['id']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "void"

    # ① 作废 ≠ 删除：原值仍在库里，显式索要全部记录时查得到（可追溯）
    all_records = client.get(
        f"/api/v1/points/{pt['id']}/records", params={"active_only": False}
    ).json()
    kept = next((r for r in all_records if r["id"] == rec["id"]), None)
    assert kept is not None, "作废把数据删掉了 —— 原始数据表只增不删是硬约束"
    assert kept["value_mm"] == 4907.0, "作废不得改动数值"

    # ② 默认列表（只给生效记录）里不该再有它。
    #    否则同一天同分量有两条时，前端回填会取到这条作废记录 → 输入框空白
    #    （用户实测报障，详见 tests/test_void_records.py）
    default = client.get(f"/api/v1/points/{pt['id']}/records").json()
    assert all(r["id"] != rec["id"] for r in default)


def test_record_not_found(client):
    assert client.put("/api/v1/records/9999", json={"remark": "x"}).status_code == 404
    assert client.delete("/api/v1/records/9999").status_code == 404


# ---------- 更正机制 ----------

def test_correct_record_keeps_old_value(client):
    """更正：旧记录原值保留并指向新记录；新记录生效且可查询到。"""
    _, pt = _project_with_points(client)
    old = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    ).json()

    resp = client.post(
        f"/api/v1/records/{old['id']}/correct",
        json={"value_mm": 4906.5, "remark": "抄录错误，更正"},
    )
    assert resp.status_code == 200
    new = resp.json()
    assert new["value_mm"] == 4906.5
    assert new["source"] == "correct"
    assert new["status"] == "normal"

    # 旧记录仍在库中，但已被标记为被替代
    old_rec = client.get(f"/api/v1/points/{pt['id']}/records", params={"active_only": False}).json()
    old_found = next(r for r in old_rec if r["id"] == old["id"])
    assert old_found["value_mm"] == 4907.0  # 原值未丢失
    assert old_found["superseded_by_id"] == new["id"]

    # 默认列表只返回生效记录（新记录）
    active = client.get(f"/api/v1/points/{pt['id']}/records").json()
    assert len(active) == 1
    assert active[0]["id"] == new["id"]


def test_after_correct_create_same_day_still_conflict(client):
    """更正后生效记录仍存在，直接再录入同日同分量依然 409（防重不破）。"""
    _, pt = _project_with_points(client)
    old = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    ).json()
    client.post(f"/api/v1/records/{old['id']}/correct", json={"value_mm": 4906.5, "remark": "更正"})

    resp = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4906.0},
    )
    assert resp.status_code == 409  # 生效记录存在，必须走更正


def test_correct_superseded_record_conflict(client):
    """已更正过的记录不能再被更正（必须操作生效记录）。"""
    _, pt = _project_with_points(client)
    old = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    ).json()
    new = client.post(f"/api/v1/records/{old['id']}/correct", json={"value_mm": 4906.5}).json()
    # 对旧记录再次更正 → 409
    resp = client.post(f"/api/v1/records/{old['id']}/correct", json={"value_mm": 1.0})
    assert resp.status_code == 409
    # 对生效记录（新记录）再更正 → 正常
    resp2 = client.post(f"/api/v1/records/{new['id']}/correct", json={"value_mm": 4906.0})
    assert resp2.status_code == 200


def test_correct_to_missing(client):
    """更正为缺测：新记录 value_mm=None 且 status=missing。"""
    _, pt = _project_with_points(client)
    old = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    ).json()
    resp = client.post(f"/api/v1/records/{old['id']}/correct", json={"value_mm": None, "remark": "读数无效"})
    assert resp.status_code == 200
    assert resp.json()["value_mm"] is None
    assert resp.json()["status"] == "missing"
