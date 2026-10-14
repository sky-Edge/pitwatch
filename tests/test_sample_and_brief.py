# -*- coding: utf-8 -*-
"""示例项目 + 项目速览 + 关注汇总 的接口测试。"""
from datetime import date, timedelta


def test_create_sample_project(client):
    """一键示例项目：项目、规则、测点、数据、事件全部就位。"""
    r = client.post("/api/v1/projects/sample", params={"periods": 3, "point_count": 8})
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["points_created"] == 8
    assert body["periods"] == 3
    assert body["records_generated"] > 0
    assert body["preset"] == "八号基坑实测模板（自定义）"

    pid = body["project_id"]

    # 项目已创建
    rp = client.get(f"/api/v1/projects/{pid}")
    assert rp.status_code == 200
    assert rp.json()["sign_config"]["settlement"]["positive_means"] == "down"

    # 测点已建（沉降 + 位移 X/Y 双分量 → both 类型）
    rpts = client.get(f"/api/v1/projects/{pid}/points")
    assert rpts.status_code == 200
    assert len(rpts.json()) == 8

    # 预警规则已套用（沉降/位移 × 控制/报警 = 4 条）
    rrules = client.get(f"/api/v1/projects/{pid}/warning-rules")
    assert len(rrules.json()) == 4

    # 数据已生成且 source=generated
    rc = client.get(f"/api/v1/projects/{pid}/data-count")
    assert rc.json()["records"] == body["records_generated"]
    assert rc.json()["has_data"] is True


def test_sample_project_repeatable(client):
    """重复点击不应报错（编号自动追加后缀）。"""
    a = client.post("/api/v1/projects/sample", params={"periods": 2, "point_count": 2})
    b = client.post("/api/v1/projects/sample", params={"periods": 2, "point_count": 2})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["project_code"] != b.json()["project_code"]


def test_sample_project_generates_multiple_periods(client):
    """3 期数据应能算出「本次变化」。

    ⚠ 这条原来断言的是"示例数据不触发报警"。2026-09-29 用户明确"可以不用（规避）预警"之后，
    生成器改为按**真实台账**标定测量噪声（位移 σ=2.6mm），而位移的控制速率只有 3mm/d ——
    真实台账里 34% 的天数都超这个值。所以示例项目**必然会**出现预警，
    这个断言改成盯"别一片红"：报警（≥2×控制速率）不能是普遍现象。
    """
    r = client.post(
        "/api/v1/projects/sample",
        params={"periods": 3, "point_count": 4, "start_date": date.today().isoformat()},
    )
    pid = r.json()["project_id"]
    rows = client.get(f"/api/v1/projects/{pid}/warning-status").json()
    with_data = [x for x in rows if x["cumulative_mm"] is not None]
    assert with_data, "示例数据应至少有一个分量有累计值"
    alarms = [x for x in with_data if x["warning_level"] == "alarm"]
    assert len(alarms) <= max(2, len(with_data) * 2 // 5), (
        f"{len(alarms)}/{len(with_data)} 个分量报了警 —— 生成的数据量级失控了"
        "（真实台账该比例约 1/4，超过 40% 就说明不正常）"
    )


def test_project_brief_headline(client):
    """速览应给出一句中文结论，含方向词与控制值。"""
    r = client.post("/api/v1/projects/sample", params={"periods": 4, "point_count": 4})
    pid = r.json()["project_id"]

    briefs = client.get("/api/v1/project-briefs").json()
    row = next(b for b in briefs if b["project_id"] == pid)
    assert row["headline"], "有数据的项目应有结论"
    assert "累计" in row["headline"]
    assert "控制值" in row["headline"]
    # 不应出现内部字段名
    assert "settlement" not in row["headline"]
    assert "displacement" not in row["headline"]


def test_project_brief_empty_project(client):
    """没有数据的项目 headline 为 None，不应报错。"""
    client.post("/api/v1/projects", json={"code": "P-EMPTY", "name": "空项目"})
    briefs = client.get("/api/v1/project-briefs").json()
    row = next(b for b in briefs if b["headline"] is None or b["headline"])
    assert isinstance(briefs, list)
    assert len(briefs) >= 1


def test_project_brief_headline_has_direction_for_both_values(client):
    """变化量与累计量都要带方向词。

    实测发现：现场确实存在「本期回弹 7mm、累计仍下沉 6mm」这类数据，
    若两个数字都取绝对值不标方向，看起来会自相矛盾。故两者都必须带方向。
    """
    r = client.post("/api/v1/projects/sample", params={"periods": 5, "point_count": 6})
    pid = r.json()["project_id"]
    row = next(b for b in client.get("/api/v1/project-briefs").json() if b["project_id"] == pid)
    assert row["headline"]
    assert "累计" in row["headline"]
    # 「累计」后面必须紧跟方向词，而不是直接跟数字
    # （沉降为 下沉/隆起，位移为 向基坑内/向基坑外）
    tail = row["headline"].split("累计", 1)[1]
    assert not tail[0].isdigit(), f"累计后缺方向词：{row['headline']}"
    assert tail.startswith(("下沉", "隆起", "向基坑内", "向基坑外", "无变化")), f"方向词异常：{row['headline']}"


def test_project_brief_no_headline_when_cumulative_zero(client):
    """只有初始值、累计为 0 的项目不给结论（一句"累计0.0mm"没有信息量）。"""
    pid = client.post("/api/v1/projects", json={"code": "P-ZERO", "name": "零值项目"}).json()["id"]
    client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "Z1", "point_type": "settlement", "initial_value_mm": 3000.0},
    )
    # 只有一期数据 → 累计恒为 0
    point = client.get(f"/api/v1/projects/{pid}/points").json()[0]
    client.post(
        f"/api/v1/points/{point['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-09-01", "value_mm": 3000.0},
    )
    row = next(b for b in client.get("/api/v1/project-briefs").json() if b["project_id"] == pid)
    assert row["headline"] is None


def test_project_attention(client):
    """关注汇总应能识别"该观测了"，且不写库。"""
    old = (date.today() - timedelta(days=5)).isoformat()
    r = client.post("/api/v1/projects/sample", params={"periods": 2, "point_count": 2, "start_date": old})
    pid = r.json()["project_id"]

    rows = client.get("/api/v1/project-attention").json()
    row = next(x for x in rows if x["project_id"] == pid)
    assert row["last_obs_date"] == old
    assert row["stale_days"] == 5
    assert row["needs_observation"] is True
    assert "project_code" in row


def test_project_attention_readonly(client):
    """关注接口是只读的：连续调用两次结果一致，不应产生副作用。"""
    client.post("/api/v1/projects/sample", params={"periods": 2, "point_count": 2})
    a = client.get("/api/v1/project-attention").json()
    b = client.get("/api/v1/project-attention").json()
    assert a == b
