# -*- coding: utf-8 -*-
"""今日进度：填数据 + 出报都做完才算「今天做完了」（用户 2026-11-16）。

现场每天的真实动作就两个：把今天的读数录进系统、把今天的日报导出去。
只做了前一半，这期还没交出去；只做了后一半，报里是旧数。所以两件齐了才算完成。

守的几条：
- 「填数据」只认**非 generated** 的记录 —— 演示数据不算干过活；
- 作废 / 被更正的不算当前成果；
- 「出报」靠 export_logs：导出/打印接口成功后落一条；
- 没做的项目不会出现在 filled/exported 里（前端据此显示「未开始」）。
"""
from datetime import date

from tests.conftest import put_control_rules

TODAY = date.today().isoformat()


def _mk_project(client, code: str) -> tuple[int, int]:
    r = client.post("/api/v1/projects", json={"code": code, "name": code})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 100.0},
    )
    assert r.status_code == 201, r.text
    put_control_rules(client, pid)
    return pid, r.json()["id"]


def _add_today(client, pt: int, value: float = 99.5) -> None:
    r = client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": TODAY, "value_mm": value},
    )
    assert r.status_code == 201, r.text


def test_filled_by_today_records(client):
    pid, pt = _mk_project(client, "甲基坑")
    _add_today(client, pt)

    body = client.get("/api/v1/daily-progress").json()
    assert body["date"] == TODAY
    assert pid in body["filled"]
    # 只填了数据、没出报 → 不算完成
    assert pid not in body["done"]


def test_export_marks_exported_and_completes(client):
    pid, pt = _mk_project(client, "乙基坑")
    _add_today(client, pt)

    r = client.get(f"/api/v1/projects/{pid}/reports/excel?obs_date={TODAY}")
    assert r.status_code == 200, r.text

    body = client.get("/api/v1/daily-progress").json()
    assert pid in body["exported"]
    assert pid in body["done"], "填了数据又导了报，应该算今天完成"


def test_generated_data_does_not_count_as_filled(client):
    """演示数据不算「今天干了活」—— 否则一按「自动生成」就全绿了。"""
    from backend.core.db import SessionLocal
    from backend.models.observation_record import ObservationRecord

    pid, pt = _mk_project(client, "丙基坑")

    db = SessionLocal()
    try:
        db.add(
            ObservationRecord(
                point_id=pt,
                series_type="settlement",
                component="value",
                obs_date=date.today(),
                value_mm=99.0,
                source="generated",
                status="normal",
            )
        )
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/daily-progress").json()
    assert pid not in body["filled"], "generated 记录不该算进「今天填了数据」"


def test_progress_empty_for_untouched_project(client):
    pid, _ = _mk_project(client, "戊基坑")
    body = client.get("/api/v1/daily-progress").json()
    assert pid not in body["filled"]
    assert pid not in body["exported"]
    assert pid not in body["done"]
