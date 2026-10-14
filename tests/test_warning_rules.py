# -*- coding: utf-8 -*-
"""Phase 3.5 预警规则配置模块测试。

覆盖：规范预设列表、项目规则整组覆盖、幂等替换、自定义限值。
"""


def _project(client) -> dict:
    resp = client.post("/api/v1/projects", json={"code": "PRJ-RULE", "name": "规则测试项目"})
    return resp.json()


def test_standards_presets_list(client):
    resp = client.get("/api/v1/warning-rules/standards")
    assert resp.status_code == 200
    presets = resp.json()
    assert len(presets) >= 3
    keys = {p["key"] for p in presets}
    assert "gb50497_l2" in keys
    # 每条预设包含沉降/位移 × 控制/报警
    for p in presets:
        indicators = {r["indicator"] for r in p["rules"]}
        levels = {r["level"] for r in p["rules"]}
        assert indicators == {"settlement", "displacement"}
        assert levels == {"control", "alarm"}


def test_upsert_project_rules(client):
    p = _project(client)
    payload = {
        "rules": [
            {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
            {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
            {"indicator": "displacement", "level": "control", "threshold_cumulative_mm": 20, "threshold_rate_mm_per_day": 3},
            {"indicator": "displacement", "level": "alarm", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 4},
        ],
        "source_note": "八号基坑监测方案",
    }
    resp = client.put(f"/api/v1/projects/{p['id']}/warning-rules", json=payload)
    assert resp.status_code == 200
    rules = resp.json()
    assert len(rules) == 4

    settle_control = next(r for r in rules if r["indicator"] == "settlement" and r["level"] == "control")
    assert settle_control["threshold_cumulative_mm"] == 30
    assert settle_control["threshold_rate_mm_per_day"] == 10
    assert settle_control["source_note"] == "八号基坑监测方案"


def test_upsert_replaces_old_rules(client):
    """整组覆盖：再次 PUT 后规则数量不变（旧规则被删除）。"""
    p = _project(client)
    payload = {"rules": [
        {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
        {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
    ]}
    assert client.put(f"/api/v1/projects/{p['id']}/warning-rules", json=payload).status_code == 200

    payload2 = {"rules": [
        {"indicator": "displacement", "level": "control", "threshold_cumulative_mm": 20, "threshold_rate_mm_per_day": 3},
    ]}
    resp = client.put(f"/api/v1/projects/{p['id']}/warning-rules", json=payload2)
    assert resp.status_code == 200
    assert len(resp.json()) == 1  # 旧沉降规则已被替换
    assert resp.json()[0]["indicator"] == "displacement"


def test_blank_preset_allows_custom_values(client):
    """自定义（空白）预设：限值全空，用户自行填写。"""
    resp = client.get("/api/v1/warning-rules/standards")
    blank = next(p for p in resp.json() if p["key"] == "blank")
    for r in blank["rules"]:
        assert r["threshold_cumulative_mm"] is None


def test_rules_for_missing_project_404(client):
    resp = client.get("/api/v1/projects/9999/warning-rules")
    assert resp.status_code == 404
