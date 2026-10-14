# -*- coding: utf-8 -*-
"""一键修正报警值（2026-09-29 加）。

用户场景：项目里出现了触发预警的观测值，一条条点进去改太费事，想要"一键"修正。

这里盯住四件最要紧的事：

1. **原值绝不消失** —— `observation_records` 只增不改是硬约束。修正必须走更正流程：
   旧记录置 `superseded` 且数值原样保留、`superseded_by_id` 指到新记录，新记录 `source='correct'`。
   任何一条 `db.delete(ObservationRecord)` 都是 bug。
2. **修完必须真的不报警** —— 判定口径要和预警引擎一致（`warnings.engine.evaluate`），
   否则会出现"点了修正、列表里还是红的"。
3. **预检不能落库** —— `dry_run` 是默认值，用户确认前不许动数据。
4. **幂等** —— 连着点两次，第二次应该是 0 条改动（否则说明修正没修干净）。
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.core.db import SessionLocal
from backend.models.observation_record import ObservationRecord

FIX_URL = "/api/v1/projects/{pid}/warning-events/fix-values"


def _setup(client, *, rate=3.0, cum=20.0, point_type="settlement", with_rules=True):
    p = client.post("/api/v1/projects", json={"code": "PRJ-FIX", "name": "修正测试"}).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points",
        json={"point_no": "JJC1", "point_type": point_type},
    ).json()
    if with_rules:
        client.put(
            f"/api/v1/projects/{p['id']}/warning-rules",
            json={
                "rules": [
                    {"indicator": "settlement", "level": "control",
                     "threshold_cumulative_mm": cum, "threshold_rate_mm_per_day": rate},
                    {"indicator": "settlement", "level": "alarm",
                     "threshold_cumulative_mm": cum + 1, "threshold_rate_mm_per_day": rate + 1},
                ],
                "source_note": "测试规范",
            },
        )
    return p["id"], pt["id"]


def _add(client, point_id: int, d: str, v: float | None):
    return client.post(
        f"/api/v1/points/{point_id}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
    ).json()


def _computed(client, point_id: int) -> list[dict]:
    return client.get(
        f"/api/v1/points/{point_id}/computed",
        params={"series_type": "settlement", "component": "value"},
    ).json()


def _all_rows(point_id: int) -> list[ObservationRecord]:
    """含 superseded / void 的全量记录（要验证原值还在）。"""
    with SessionLocal() as db:
        return (
            db.query(ObservationRecord)
            .filter(ObservationRecord.point_id == point_id)
            .order_by(ObservationRecord.id)
            .all()
        )


def _dry(client, pid: int, **params):
    return client.post(FIX_URL.format(pid=pid), params={"dry_run": True, **params}).json()


def _apply(client, pid: int, **params):
    return client.post(FIX_URL.format(pid=pid), params={"dry_run": False, **params}).json()


# ────────────────────────── 1. 预检 ──────────────────────────


def test_dry_run_is_default_and_does_not_touch_data(client):
    """不传 dry_run 也必须只预检 —— 这个接口会改观测数据，默认值必须是安全的那一侧。"""
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)          # 单期 10mm，控制速率 3mm/d

    before = [(r.id, r.value_mm, r.status) for r in _all_rows(pt)]
    r = client.post(FIX_URL.format(pid=pid)).json()  # 不带任何参数
    assert r["dry_run"] is True
    assert r["fixable"] == 1 and r["changed"] == 0
    assert [(x.id, x.value_mm, x.status) for x in _all_rows(pt)] == before, (
        "预检动了数据！"
    )


def test_dry_run_reports_old_and_new_values(client):
    """预检要给出「旧值 → 新值」对照 —— 不给对照，用户没法判断点下去会改成什么。"""
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)

    r = _dry(client, pid)
    s = r["samples"][0]
    assert s["obs_date"] == "2026-08-02"
    assert s["old_value_mm"] == 1010.0
    assert s["new_value_mm"] == 1002.0, "应压到控制速率(3mm/d)以内并向下取整到整数 mm"
    assert s["level"] == "alarm", "单期 10mm 同时超过控制值(3)与报警值(4)，等级取高"
    assert r["by_level"] == {"alarm": 1, "control": 0}


# ────────────────────────── 2. 执行与「原值必须还在」 ──────────────────────────


def test_fix_brings_series_back_and_keeps_original_value(client):
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)
    _add(client, pt, "2026-08-03", 1015.0)

    r = _apply(client, pid)
    assert r["changed"] >= 1

    # ① 修完不再有任何非 normal
    bad = [x for x in _computed(client, pt) if x["warning_level"] != "normal"]
    assert not bad, f"修正后仍然报警：{bad}"

    # ② 原值一条都没丢（superseded 保留、指向新记录）
    rows = _all_rows(pt)
    old = next(x for x in rows if x.obs_date.isoformat() == "2026-08-02" and x.status == "superseded")
    assert old.value_mm == 1010.0, "旧值被改动了 —— 修正必须只增不改"
    assert old.superseded_by_id is not None
    new = next(x for x in rows if x.id == old.superseded_by_id)
    assert new.source == "correct" and new.value_mm == 1002.0
    assert old.remark is None or "一键修正" not in (old.remark or "")  # 说明写在新记录上


def test_fix_leaves_a_trace_in_remark(client):
    """更正记录必须留下「原值 → 新值」说明，否则过一阵没人知道这数被改过。"""
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)
    _apply(client, pid)

    rows = _all_rows(pt)
    new = next(x for x in rows if x.source == "correct")
    assert "一键修正" in (new.remark or "")
    assert "1010" in new.remark and "1002" in new.remark, f"说明里没写清改了什么：{new.remark}"


def test_remark_has_no_scientific_notation(client):
    """更正备注必须能看出改了啥。

    踩过的坑：值用  格式化，位移坐标那种大数写成 ，
    备注长成「5.10024e+08 → 5.10024e+08 mm」—— 改前改后看起来一样（生产库里 189 条都这样）。
    """
    pid, pt = _setup(client, rate=3.0, cum=500000.0)
    big = 3412987.154
    _add(client, pt, "2026-08-01", big)
    _add(client, pt, "2026-08-02", big + 10)      # 单期 10mm → 报警
    _apply(client, pid)
    row = next(x for x in _all_rows(pt) if x.source == "correct")
    remark = row.remark or ""
    assert "e+" not in remark and "e-" not in remark, f"备注里有科学计数法：{remark}"
    import re

    m = re.search(r"：(\d+(?:\.\d+)?) → (\d+(?:\.\d+)?) mm", remark)
    assert m, f"备注里的「旧值 → 新值」读不出来：{remark}"
    assert m.group(1) == "3412997.154", f"旧值不对：{m.group(1)}"
    assert m.group(1) != m.group(2), f"旧值和新值一样 —— 用户看不出改了什么：{remark}"
    assert m.group(2).startswith("34129"), f"新值不对：{m.group(2)}"


def test_fix_never_deletes_records(client):
    """修正只增加记录条数（superseded + 新记录），绝不减少。"""
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)
    _add(client, pt, "2026-08-03", 1020.0)
    n_before = len(_all_rows(pt))
    _apply(client, pid)
    assert len(_all_rows(pt)) == n_before + 2, "修正应当只增不删"


def test_fix_is_idempotent(client):
    """连点两次，第二次应该没有可改的了。"""
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)
    first = _apply(client, pid)
    assert first["changed"] >= 1
    second = _apply(client, pid)
    assert second["fixable"] == 0 and second["changed"] == 0, (
        f"修正不彻底，第二次还要改 {second['fixable']} 条：{second['samples'][:2]}"
    )


# ────────────────────────── 3. 修正范围 ──────────────────────────


def test_only_alarm_when_include_control_false(client):
    """`include_control=false` 时只修报警（4mm/d），预警（3mm/d）保留不动。"""
    pid, pt = _setup(client, rate=3.0, cum=50.0)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1003.0)   # 速率 3 → 预警
    r = _dry(client, pid, include_control=False)
    assert r["fixable"] == 0, "只修报警时不该动预警那一条"

    _add(client, pt, "2026-08-03", 1013.0)   # 速率 10 → 报警
    r2 = _dry(client, pid, include_control=False)
    assert r2["fixable"] == 1 and r2["by_level"]["alarm"] == 1
    assert [s["obs_date"] for s in r2["samples"]] == ["2026-08-03"]


def test_default_fixes_control_too(client):
    """默认（不带参数）应当连预警一起修干净。"""
    pid, pt = _setup(client, rate=3.0, cum=50.0)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1003.0)
    r = _apply(client, pid)
    assert r["by_level"]["control"] == 1
    assert not [x for x in _computed(client, pt) if x["warning_level"] != "normal"]


# ────────────────────────── 4. 不碰不该碰的东西 ──────────────────────────


def test_missing_and_voided_records_are_untouched(client):
    """缺测期与已作废记录不参与修正（作废的读数不该被"修活"）。"""
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    voided = _add(client, pt, "2026-08-02", 1090.0)
    client.delete(f"/api/v1/records/{voided['id']}")     # 作废
    _add(client, pt, "2026-08-03", None)                 # 缺测
    _add(client, pt, "2026-08-04", 1001.0)

    r = _dry(client, pid)
    assert r["fixable"] == 0, f"作废/缺测被算进来了：{r['samples']}"
    voided_row = next(x for x in _all_rows(pt) if x.id == voided["id"])
    assert voided_row.status == "void" and voided_row.value_mm == 1090.0


def test_no_rules_project_reports_nothing_to_fix(client):
    """没配规则的项目：既没有预警事件，也不该被"修正"（无从定义安全值）。"""
    pid, pt = _setup(client, with_rules=False)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 2000.0)   # 数值很离谱，但没有规则
    r = _dry(client, pid)
    assert r["fixable"] == 0 and r["unfixable"] == 0


def test_real_data_is_corrected_but_preserved(client):
    """真实录入的数据同样会被修正（用户点了就是同意），但原值必须完整保留。"""
    pid, pt = _setup(client)
    rec = _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)
    assert rec["source"] != "generated"
    _apply(client, pid)
    rows = _all_rows(pt)
    superseded = [x for x in rows if x.status == "superseded"]
    assert superseded, "真实数据被修正后也必须以 superseded 保留原值"
    assert superseded[0].value_mm == 1010.0


# ────────────────────────── 5. 与预警事件联动 ──────────────────────────


def test_warning_events_are_rebuilt_after_fix(client, ):
    """改完数据必须顺手重建预警事件，否则列表里还挂着一堆红。"""
    pid, pt = _setup(client)
    _add(client, pt, "2026-08-01", 1000.0)
    _add(client, pt, "2026-08-02", 1010.0)
    client.post(f"/api/v1/projects/{pid}/warning-events/recompute")
    assert client.get(f"/api/v1/projects/{pid}/warning-events").json(), "先得有事件可修"

    r = _apply(client, pid)
    assert "recompute" in r
    assert client.get(f"/api/v1/projects/{pid}/warning-events").json() == [], (
        "修正后预警事件没有重建，列表里还是红的"
    )


# ────────────────────────── 6. 源码守卫：只增不改 ──────────────────────────


def test_service_never_deletes_observation_records():
    """源码守卫：修正服务里不允许出现对 observation_records 的物理删除。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "backend" / "services" / "warning_fix_service.py").read_text(encoding="utf-8")
    assert "db.delete" not in src, "修正服务里出现了物理删除 —— 原始数据只增不改"
    assert "observation_repo.correct(" in src, "修正必须走 observation_repo.correct() 的更正流程"
    assert "evaluate(" in src, "判定口径必须复用 Warning Engine，不能另写一套比较逻辑"
    assert "compute_series(" in src, "计算口径必须复用 Calculation Engine，不能另写一套 delta/累计/速率"


def test_frontend_shows_fix_result_outside_the_modal():
    """源码守卫：修正结果必须渲染在**页面上**，不能只留在确认弹窗里。

    数据生成功能踩过这个坑 —— 结果放进弹窗，而弹窗在动作结束后就被关掉了，
    用户什么都看不到，只剩 3 秒 toast，以为"点了没反应"。修正是不可逆的写操作，
    更需要把「改了什么」留在页面上。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "frontend" / "src" / "pages" / "WarningEventsPage.tsx").read_text(encoding="utf-8")
    assert "一键修正报警值" in src, "预警页没有这个入口"
    assert "setOutcome(r)" in src, "执行结果没有存到页面状态里"
    assert "outcome &&" in src and src.index("outcome &&") < src.index("<Modal"), (
        "结果卡片被放进弹窗（或排在弹窗之后）—— 弹窗一关用户就看不到改了什么"
    )
    assert "原读数已作废保留" in src, "必须明确告诉用户原数据没被删掉"
