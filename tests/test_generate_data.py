# -*- coding: utf-8 -*-
"""自动生成观测数据：日期冲突的预检与替换（2026-09-29 加）。

背景：原先遇到"这天已经有数据"是**静默跳过**，用户不知道所选范围里哪些日期没生成。
现在改为先预检、再由用户决定：跳过 / 只替换自动生成的 / 连真实数据一起替换。

这里盯住三件最要紧的事：

1. **真实数据不许消失** —— `replace_all` 只能把真实记录置为 `void`（作废），
   数值必须原样留在库里。原始数据表"只增不删"是项目的硬约束。
2. **不许撞唯一索引** —— 作废后的记录必须让位，新记录才插得进去
   （`uq_obs_active` 只约束 status 不在 void/superseded 的行）。
3. **作废过的记录不算"已有数据"** —— 否则用户替换一次之后，
   每次再生成都会被告知"这些日期有数据"（其实是自己刚作废的）。
"""
from __future__ import annotations

import pytest

from backend.core.db import SessionLocal
from backend.models.observation_record import ObservationRecord
from backend.services import data_generator_service as gen


def _setup(client, point_type: str = "settlement", with_rules: bool = True,
           rate: float = 2.0, disp_rate: float | None = None) -> tuple[int, int]:
    p = client.post("/api/v1/projects", json={"code": "PRJ-GEN", "name": "生成测试"}).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points",
        json={"point_no": "JJC1", "point_type": point_type},
    ).json()
    if with_rules:
        rules = [
            {"indicator": "settlement", "level": "control",
             "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": rate},
            {"indicator": "settlement", "level": "alarm",
             "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": rate * 2},
        ]
        if disp_rate is not None:
            rules += [
                {"indicator": "displacement", "level": "control",
                 "threshold_cumulative_mm": 20, "threshold_rate_mm_per_day": disp_rate},
                {"indicator": "displacement", "level": "alarm",
                 "threshold_cumulative_mm": 21, "threshold_rate_mm_per_day": disp_rate + 1},
            ]
        client.put(
            f"/api/v1/projects/{p['id']}/warning-rules",
            json={"rules": rules, "source_note": "测试规范"},
        )
    return p["id"], pt["id"]


def _add_record(client, point_id: int, d: str, v: float | None, source: str = "manual",
                series_type: str = "settlement", component: str = "value"):
    """走 API 加一条记录（API 的 source 是固定的，真实数据用它就够）。"""
    return client.post(
        f"/api/v1/points/{point_id}/records",
        json={"series_type": series_type, "component": component,
              "obs_date": d, "value_mm": v},
    )


def _rows(point_id: int, include_replaced: bool = False) -> list[ObservationRecord]:
    """直接查库（要看到 void/superseded 的记录）。"""
    with SessionLocal() as db:
        q = db.query(ObservationRecord).filter(ObservationRecord.point_id == point_id)
        if not include_replaced:
            q = q.filter(ObservationRecord.status.notin_(("void", "superseded")))
        return q.order_by(ObservationRecord.obs_date).all()


# ────────────────────────── 1. 预检 ──────────────────────────


def test_preview_all_empty(client):
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-07-01", 5000.0)  # 范围外的记录不该算进来
    r = client.get(f"/api/v1/projects/{pid}/generate-data/preview",
                   params={"start": "2026-08-01", "end": "2026-08-05"}).json()
    assert r["total_days"] == 5
    assert r["empty_days"] == 5
    assert r["occupied_days"] == []
    assert r["real_data_days"] == [] and r["generated_only_days"] == []


def test_preview_classifies_generated_vs_real(client):
    """预检要能分清"自动生成的演示数据"和"真实数据" —— 两者处理方式完全不同。"""
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)          # 真实（manual）
    _add_record(client, pt, "2026-08-02", 4999.0)          # 真实（manual）
    # 手工塞一条 generated 的，模拟"之前自动生成过"
    with SessionLocal() as db:
        db.add(ObservationRecord(point_id=pt, series_type="settlement", component="value",
                                 obs_date=__import__("datetime").date(2026, 8, 3),
                                 value_mm=4998.0, status="normal", source="generated"))
        db.commit()

    r = client.get(f"/api/v1/projects/{pid}/generate-data/preview",
                   params={"start": "2026-08-01", "end": "2026-08-05"}).json()
    kinds = {d["date"]: d["kind"] for d in r["occupied_days"]}
    assert kinds == {"2026-08-01": "real", "2026-08-02": "real", "2026-08-03": "generated"}
    assert r["real_data_days"] == ["2026-08-01", "2026-08-02"]
    assert r["generated_only_days"] == ["2026-08-03"]
    assert r["empty_days"] == 2


def test_preview_counts_mixed_day_as_real(client):
    """同一天既有真实又有演示数据 → 归入"真实"（保守：宁可不让替换）。"""
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    with SessionLocal() as db:
        db.add(ObservationRecord(point_id=pt, series_type="settlement", component="X",
                                 obs_date=__import__("datetime").date(2026, 8, 1),
                                 value_mm=1.0, status="normal", source="generated"))
        db.commit()
    r = client.get(f"/api/v1/projects/{pid}/generate-data/preview",
                   params={"start": "2026-08-01", "end": "2026-08-01"}).json()
    assert r["occupied_days"][0]["kind"] == "mixed"
    assert r["real_data_days"] == ["2026-08-01"]


def test_preview_ignores_voided_records(client):
    """已作废的记录不算"已有数据" —— 否则替换一次后每次都被问。"""
    pid, pt = _setup(client)
    with SessionLocal() as db:
        db.add(ObservationRecord(point_id=pt, series_type="settlement", component="value",
                                 obs_date=__import__("datetime").date(2026, 8, 1),
                                 value_mm=5000.0, status="void", source="manual"))
        db.commit()
    r = client.get(f"/api/v1/projects/{pid}/generate-data/preview",
                   params={"start": "2026-08-01", "end": "2026-08-02"}).json()
    assert r["occupied_days"] == [], "作废记录不该出现在冲突提示里"
    assert r["empty_days"] == 2


def test_preview_rejects_reversed_range(client):
    pid, _ = _setup(client)
    assert client.get(f"/api/v1/projects/{pid}/generate-data/preview",
                      params={"start": "2026-08-05", "end": "2026-08-01"}).status_code == 422


# ────────────────────────── 2. skip（默认，最安全） ──────────────────────────


def test_skip_leaves_existing_data_untouched(client):
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    _add_record(client, pt, "2026-08-02", 4999.0)

    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-08-01", "end": "2026-08-04", "seed": 1}).json()
    assert r["on_conflict"] == "skip"
    assert r["replaced"] == 0 and r["voided_real"] == 0
    assert set(r["kept_dates"]) == {"2026-08-01", "2026-08-02"}

    rows = _rows(pt)
    dates = [x.obs_date.isoformat() for x in rows]
    assert dates == ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"]
    # 已有两天的原值没被动过
    assert rows[0].value_mm == 5000.0 and rows[1].value_mm == 4999.0
    assert {x.source for x in rows[2:]} == {"generated"}


# ────────────────────────── 3. 只替换自动生成的数据 ──────────────────────────


def test_replace_generated_only_touches_generated(client):
    """真实数据一条都不能动；自动生成的那天被删掉重建。"""
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)  # 真实
    with SessionLocal() as db:
        db.add(ObservationRecord(point_id=pt, series_type="settlement", component="value",
                                 obs_date=__import__("datetime").date(2026, 8, 2),
                                 value_mm=4998.0, status="normal", source="generated"))
        db.commit()

    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-08-01", "end": "2026-08-03",
                            "seed": 1, "on_conflict": "replace_generated"}).json()
    assert r["replaced"] == 1 and r["voided_real"] == 0
    assert "2026-08-01" in r["kept_dates"], "真实数据那天应被跳过"

    rows = _rows(pt)
    real = [x for x in rows if x.obs_date.isoformat() == "2026-08-01"]
    gen2 = [x for x in rows if x.obs_date.isoformat() == "2026-08-02"]
    assert real[0].value_mm == 5000.0 and real[0].source != "generated", "真实数据被改动了！"
    assert gen2 and gen2[0].source == "generated", "自动生成的那天应被重建"


# ────────────────────────── 4. 连真实数据一起替换（关键：原值必须留） ──────────────────────────


def test_replace_all_voids_real_data_and_keeps_original_value(client):
    """真实数据被替换后，**原值必须还在库里**（只作废、不删除）。"""
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    _add_record(client, pt, "2026-08-02", 4999.0)

    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-08-01", "end": "2026-08-03",
                            "seed": 1, "on_conflict": "replace_all"}).json()
    assert r["replaced"] == 2 and r["voided_real"] == 2, f"应作废 2 条真实数据：{r}"

    # 生效记录里只剩新生成的
    active = _rows(pt)
    assert len(active) == 3
    assert {x.source for x in active} == {"generated"}

    # 但原值还在（作废态）
    allrows = _rows(pt, include_replaced=True)
    voided = [x for x in allrows if x.status == "void"]
    assert len(voided) == 2, f"被替换的真实数据不见了：{[ (x.obs_date, x.status, x.value_mm) for x in allrows]}"
    assert {x.value_mm for x in voided} == {5000.0, 4999.0}, "作废记录的数值必须原样保留"
    assert all(x.remark for x in voided), "作废时应留下说明，便于追溯"


def test_replace_all_does_not_break_unique_index(client):
    """作废让位后新记录必须能插进去（否则会撞 uq_obs_active）。"""
    pid, pt = _setup(client)
    for d in ("2026-08-01", "2026-08-02"):
        _add_record(client, pt, d, 5000.0)
    # 连续两次"全部替换"，第二次仍应成功（说明第一次的作废确实让位了）
    for _ in range(2):
        resp = client.post(f"/api/v1/projects/{pid}/generate-data",
                           params={"start": "2026-08-01", "end": "2026-08-02",
                                   "seed": 7, "on_conflict": "replace_all"})
        assert resp.status_code == 200, resp.text
    active = _rows(pt)
    assert len(active) == 2, "每天只该有一条生效记录"


def test_voided_days_not_reported_as_conflict_again(client):
    """替换过一次后，同样的范围再预检不该再报冲突（否则用户会被反复追问）。"""
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    client.post(f"/api/v1/projects/{pid}/generate-data",
                params={"start": "2026-08-01", "end": "2026-08-02",
                        "seed": 1, "on_conflict": "replace_all"})
    r = client.get(f"/api/v1/projects/{pid}/generate-data/preview",
                   params={"start": "2026-08-01", "end": "2026-08-02"}).json()
    assert r["real_data_days"] == [], "作废的真实数据不该再次被当作冲突"
    # 但那天现在有生成的数据了，所以仍是"已占用"（kind=generated）
    assert r["generated_only_days"] == ["2026-08-01", "2026-08-02"]


# ────────────────────────── 5. 参数校验 ──────────────────────────


@pytest.mark.parametrize("mode", ["", "replace", "ALL", "delete"])
def test_invalid_on_conflict_rejected(client, mode: str):
    pid, _ = _setup(client)
    resp = client.post(f"/api/v1/projects/{pid}/generate-data",
                       params={"start": "2026-08-01", "end": "2026-08-02", "on_conflict": mode})
    assert resp.status_code == 422


def test_generate_requires_points(client):
    p = client.post("/api/v1/projects", json={"code": "PRJ-EMPTY", "name": "无测点"}).json()
    resp = client.post(f"/api/v1/projects/{p['id']}/generate-data",
                       params={"start": "2026-08-01", "end": "2026-08-02"})
    assert resp.status_code == 422
    assert "测点" in resp.json()["detail"]


def test_preview_unknown_project_404(client):
    assert client.get("/api/v1/projects/9999/generate-data/preview",
                      params={"start": "2026-08-01", "end": "2026-08-02"}).status_code == 404


# ───────── 6. 生成值仍然有界（"不触发预警"已放开，但防呆兜底必须还在） ─────────


def test_generated_values_respect_safety_ceiling_and_cumulative_cap(client):
    """放开"不触发预警"之后仍然要有的两条底线。

    2026-09-29 用户点头"可以不用预警"，于是**测量噪声不再被限值压住**
    （否则位移就只有 0/±1/±2，见第 9 节）。但另外两道闸不能一起拆掉：
      ① 单期变化 ≤ 3×控制速率（防呆，防止配置写错生成出天文数字）；
      ② 累计变化 ≤ 控制累计 —— 这条**照旧**，真实台账的累计也在控制值以内。
    """
    pid, pt = _setup(client)          # 控制 30mm / 2mm·d⁻¹
    _add_record(client, pt, "2026-07-31", 5000.0)
    client.post(f"/api/v1/projects/{pid}/generate-data",
                params={"start": "2026-08-01", "end": "2026-08-30", "seed": 42})

    rows = _rows(pt)
    vals = [x.value_mm for x in rows]
    deltas = [abs(b - a) for a, b in zip(vals, vals[1:])]
    assert max(deltas) <= 6.0, (       # 防呆上限 = max(3, 2×3) = 6mm
        f"单期变化冲破了防呆上限（3×控制速率=6mm）：{max(deltas)}mm"
    )
    assert max(abs(v - vals[0]) for v in vals) <= 30.0, "累计变化越过了控制累计"


# ────────────────────────── 7. 判据只有一份（源码守卫） ──────────────────────────


def test_occupied_judgement_has_single_source_of_truth():
    """「这天算不算已有数据」的判据只能写一份，且预检与生成都必须走它。

    曾经两处各写一遍过滤，结果改了一处漏一处 ——
    界面上会出现"预检说有数据、生成时又不认"（或反过来）这种自相矛盾。
    这条守卫就是被一次注入测试逼出来的。
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "backend" / "services" / "data_generator_service.py"
    ).read_text(encoding="utf-8")

    assert src.count("status not in _REPLACED_STATUS") == 1, (
        "「已作废/已更正不算占用」的判断被复制成了多份，请统一用 _counts_as_occupied()"
    )
    # 定义 1 处 + 预检 1 处 + 取占用记录 1 处
    assert src.count("_counts_as_occupied(") >= 3, (
        "预检与生成没有共用 _counts_as_occupied()，会出现两条路径判据不一致"
    )


# ────────────── 8. 序列连续性（2026-09-29 修的真实缺陷） ──────────────


def test_generation_starts_from_value_before_range(client):
    """重做中间一段时，起点必须是「范围开始日**之前**」那一期的值。

    原缺陷：起点取全历史**最后一期**的值。库里 08-09~08-14 都有数据、
    用户只重做 08-10~08-14 时，第一天会接在 08-14 的读数后面 ——
    相当于让时间倒流，整段数据平移错位，还跟 08-09 断开。
    """
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-09", 5000.0)          # 范围前的一期（真正的起点）
    for i, d in enumerate(["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]):
        _add_record(client, pt, d, 9990.0 + i)             # 范围内的旧值，故意离谱

    # 这里的数据是走 API 建的（source=manual，属真实数据），
    # 所以要选 replace_all —— replace_generated 只动演示数据，碰上真实数据一律不碰。
    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-10", "end": "2026-08-14", "seed": 3,
                "on_conflict": "replace_all"},
    ).json()
    assert r["replaced"] == 5
    assert r["voided_real"] == 5, "真实数据只能作废保留、不能物理删除"

    rows = _rows(pt)
    first = next(x for x in rows if x.obs_date.isoformat() == "2026-08-10")
    # 判据是"接在范围前一期(5000)附近"，不是"单期变化很小"：
    # 首日变化 = 趋势(开挖期可能 3~4mm) + 测量噪声(±2.3) + 偶发扰动，正常上限约 10mm。
    # 取错起点的特征值是 9994 - 5000 = 4994mm，两个量级差着两个数量级。
    assert abs(first.value_mm - 5000) <= 10, (
        f"起点取错了：08-10 生成值 {first.value_mm}，"
        f"应当接在范围前一期（08-09 的 5000）附近，而不是范围内的最后一期（9994）"
    )


def test_skip_keeps_sequence_continuous(client):
    """「跳过已有数据」时，那条已有数据要成为后续生成的起点。

    否则"跳过 3 天、从第 4 天接着生成"会让数值跳回旧起点，序列断开。
    """
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-09", 5000.0)
    for d in ["2026-08-10", "2026-08-11", "2026-08-12"]:
        _add_record(client, pt, d, 5300.0)                 # 明显偏离起点，便于判断有没有接上

    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-10", "end": "2026-08-14", "seed": 3, "on_conflict": "skip"},
    ).json()
    assert r["generated"] == 2                              # 只生成 08-13 / 08-14
    assert sorted(r["kept_dates"]) == ["2026-08-10", "2026-08-11", "2026-08-12"]

    rows = _rows(pt)
    nxt = next(x for x in rows if x.obs_date.isoformat() == "2026-08-13")
    # 同上：判据是"接在 08-12 的 5300 附近"，不是"单期变化很小"。
    # 断开的特征值是 300mm（跳回旧起点 5000），正常单期变化上限约 10mm。
    assert abs(nxt.value_mm - 5300) <= 10, (
        f"跳过已有数据后序列断开：08-13 生成值 {nxt.value_mm}，应接在 08-12 的 5300 附近"
    )


def test_changed_samples_exposes_old_and_new(client):
    """替换必须给出「旧值 → 新值」对照。

    替换前后读数只差 1~3mm，记录数/日期/测点数完全不变 ——
    只回报一个条数的话，用户会以为"点了替换没反应"。
    """
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-09", 5000.0)
    for d in ["2026-08-10", "2026-08-11"]:
        _add_record(client, pt, d, 5200.0)

    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-10", "end": "2026-08-11", "seed": 9,
                "on_conflict": "replace_all"},
    ).json()
    samples = r["changed_samples"]
    assert len(samples) == 2, f"应给出 2 条对照，实际 {len(samples)}"
    for s in samples:
        assert s["old_value_mm"] == 5200.0, "旧值必须是替换前的原始读数"
        assert s["old_source"] == "manual", "要标出旧数据是真实数据还是演示数据"
        assert s["action"] == "voided", "真实数据只能作废，不能删除"
        assert s["new_value_mm"] is not None and s["new_value_mm"] != 5200.0, "新值应已变化"
        assert s["obs_date"] in ("2026-08-10", "2026-08-11")


def test_changed_samples_empty_when_nothing_replaced(client):
    """只填补空白日期（skip）时不该有对照样例 —— 没有旧值可比。"""
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-09", 5000.0)
    _add_record(client, pt, "2026-08-10", 5000.0)

    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-10", "end": "2026-08-12", "seed": 4, "on_conflict": "skip"},
    ).json()
    # 范围 3 天：08-10 已有（跳过），08-11、08-12 是空的（生成 2 天）
    assert r["generated"] == 2 and r["kept_dates"] == ["2026-08-10"]
    assert r["changed_samples"] == []


# ────── 9. 生成数据的形态（2026-09-29 三次改：对齐真实台账 + 不许有单调趋势） ──────
#
# 三轮用户反馈把这块逼出来的：
#   ① 「生成的数据速率都一样，没参考价值」→ 从均匀随机游走换成收敛曲线；
#   ② 「真实一点，感觉全是正负 1、正负 2」
#      → 真正的毛病是**噪声幅度锚错了对象**：原来取「控制速率 ÷ 4」，
#        而大多数项目没配预警规则 → 兜底 (30, 5) → 半幅只有 1.25mm，
#        `round()` 之后日变化量只剩 {−1, 0, +1}。
#        **真实幅度是测量重复性决定的，跟规范限值没关系。**
#   ③ 「不要出现持续单调递增或递减，避免形成明显的趋势；要在一定范围内合理波动，
#      包含上涨和下跌的交替变化」
#      → 去掉确定性趋势，改成**围绕初值的均值回复游走** + 连涨连跌限制。
#      这与真实台账吻合：八号 21 期的累计量就是在一段区间里来回走，不是单调变形。
#
# 真实台账实测（八号基坑 21 期、168 个相邻期差，用 parser.parse_workbook 量的）：
#   沉降   平均|Δ|=1.20mm  0:20% ±1:42% ±2:38% ±3:1%   21 期累计跨度 9~12mm  最长同向游程平均 4.1
#   位移X  平均|Δ|=2.09mm  0:10% ±1:29% ±2:27% ±3:20% ±4:9%                  最长同向游程平均 3.9
# 生成侧（30 天 / 6 seed 平均）：沉降 平均 1.16mm、跨度 9mm、游程 4.1；
#                                位移 平均 2.10mm、跨度 17mm、游程 4.4 —— 逐项都落在实测量级。

# 「最长同向游程」的上限，取自实测最长 6 期，留 1 期余量。
_MAX_REAL_RUN = 7


def _computed(client, point_id: int, series_type: str, component: str) -> list[dict]:
    return client.get(
        f"/api/v1/points/{point_id}/computed",
        params={"series_type": series_type, "component": component},
    ).json()


def _series(client, point_id: int) -> list[dict]:
    return _computed(client, point_id, "settlement", "value")


def _displacement_series(client, point_id: int) -> list[dict]:
    return _computed(client, point_id, "displacement", "X")


def _longest_run(cums: list[float]) -> int:
    """累计序列里"连续同向变化"的最长长度（0 变化视为打断单调性）。"""
    best = run = sign = 0
    for a, b in zip(cums, cums[1:]):
        d = b - a
        if d == 0:
            run = sign = 0
            continue
        s = 1 if d > 0 else -1
        run = run + 1 if s == sign else 1
        sign = s
        best = max(best, run)
    return best


def test_no_long_monotonic_runs(client):
    """不能出现"持续单调递增 / 递减" —— 用户明确要求"涨跌交替、无明显规律性"。

    判据直接取自真实台账（逐点最长同向游程 [3,3,4,4,4,4,5,6]，最长 6、平均 4.1）。
    生成的数据要落在这个量级 —— 比真实还"交替"就成了人造锯齿，比真实还长就是这次要修的问题。
    """
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    client.post(f"/api/v1/projects/{pid}/generate-data",
                params={"start": "2026-08-02", "end": "2026-09-30", "seed": 6})

    cums = [x["cumulative_mm"] for x in _series(client, pt) if x["cumulative_mm"] is not None]
    assert len(cums) >= 40
    run = _longest_run(cums)
    assert run <= _MAX_REAL_RUN, (
        f"出现了 {run} 期连续同向变化（真实台账最长 6 期）—— 又形成单调趋势了"
    )


def test_cumulative_oscillates_around_baseline(client):
    """累计量要围绕初值上下波动，不能一直朝一个方向走。

    两个判据都从真实台账量出来：
    ① 跨度落在真实量级（沉降 21 期 9~12mm）—— 太小说明被压平，太大说明跑飞；
    ② 序列要**穿过初值**（真的在两侧摆动）—— 单调趋势是一条穿不过去的。
    """
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    client.post(f"/api/v1/projects/{pid}/generate-data",
                params={"start": "2026-08-02", "end": "2026-09-30", "seed": 5})

    cums = [x["cumulative_mm"] for x in _series(client, pt) if x["cumulative_mm"] is not None]
    span = max(cums) - min(cums)
    assert 4.0 <= span <= 45.0, f"整段累计跨度 {span:.0f}mm —— 真实台账 30 期约 10~20mm"

    crossings = sum(1 for a, b in zip(cums, cums[1:]) if a != 0 and b != 0 and (a > 0) != (b > 0))
    assert crossings >= 1, (
        f"整段一次都没穿过初值（累计 {cums[:12]}…）—— 这就是一条单调的趋势线"
    )


def test_flake_direction_is_not_always_the_same():
    """偶发扰动的方向必须随机。

    原先它跟着趋势方向走（为了"别把趋势冲散"）；现在没有趋势了，跟着走会攒出
    一连串同向的大跳 —— 正好和"涨跌交替"相反。
    """
    import random

    rng = random.Random(1)
    signs = set()
    for _ in range(4000):
        v = gen._flake_mm(rng)
        if v != 0:
            signs.add(1 if v > 0 else -1)
    assert signs == {1, -1}, f"偶发扰动只会朝一个方向：{signs}"


def test_generated_displacement_matches_real_ledger(client):
    """位移的日变化量也要对得上真实台账（散布更大、近似正态）。

    实测：0:10% ±1:29% ±2:27% ±3:20% ±4:9% ±5:5%，平均 |Δ| = 2.09mm。
    位移控制速率只有 3mm/d，真实数据里 ±3 以上占 34%（全是预警）——
    用户明确"可以不用预警"之后才允许生成到这个量级。
    """
    pid, pt = _setup(client, point_type="displacement", rate=10.0, disp_rate=3.0)
    _add_record(client, pt, "2026-07-31", 5000.0,
                series_type="displacement", component="X")
    deltas = []
    for seed in (1, 2, 3, 5, 8):
        client.post(
            f"/api/v1/projects/{pid}/generate-data",
            params={"start": "2026-08-01", "end": "2026-08-31", "seed": seed,
                    "on_conflict": "replace_all"},
        )
        rows = _displacement_series(client, pt)
        deltas += [abs(x["delta_mm"]) for x in rows if x["delta_mm"] is not None]
    n = len(deltas)
    assert n >= 100, f"样本太少（{n}）"
    avg = sum(deltas) / n
    assert 1.6 <= avg <= 2.6, f"位移平均日变化 {avg:.2f}mm 偏离实测 2.09mm 太远"
    share_ge3 = sum(1 for d in deltas if d >= 3) / n
    assert 0.10 <= share_ge3 <= 0.55, (
        f"|Δ|≥3 占 {share_ge3:.0%}，真实台账是 34% —— 少了是被压平了，多了是量级失控"
    )
    assert max(deltas) <= 12, f"出现真实台账里不会有的单期变化：{max(deltas)}mm"


def test_injection_a_one_way_drift_would_be_caught(client, monkeypatch):
    """注入回归自测：把均值回复换成一个**单向漂移**（等价于又造出一条趋势）。

    证明上面两条守卫不是摆设 —— 注入之后至少要有一条判据报警
    （单调游程变长 / 累计不再穿过初值 / 跨度冲出合理范围）。
    """
    monkeypatch.setattr(gen, "_revert_mm", lambda cumulative, plan: 2.5)   # 每期固定 +2.5mm
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    client.post(f"/api/v1/projects/{pid}/generate-data",
                params={"start": "2026-08-02", "end": "2026-09-30", "seed": 5})

    cums = [x["cumulative_mm"] for x in _series(client, pt) if x["cumulative_mm"] is not None]
    span = max(cums) - min(cums)
    crossings = sum(1 for a, b in zip(cums, cums[1:]) if a != 0 and b != 0 and (a > 0) != (b > 0))
    run = _longest_run(cums)
    assert run > _MAX_REAL_RUN or crossings == 0 or span > 45.0, (
        f"注入单向漂移后守卫毫无反应（游程 {run}、过零 {crossings} 次、跨度 {span:.0f}mm）—— "
        "说明这几条判据抓不住'出现了明显趋势'"
    )


def test_noise_amplitude_anchored_to_real_ledger_not_to_limits():
    """噪声幅度必须是「实测值」常量，不能从规范限值折算出来。

    这就是"全是正负 1、正负 2"的根因：曾经写成 `控制速率 / 4`，
    无规则项目兜底 5mm/d → 半幅 1.25mm → 取整后只剩 {−1,0,+1}。
    沉降与位移的**误差形态不同**（水准仪近似均匀、全站仪近似正态），所以是每指标一套参数。
    """
    for series_type, (kind, scale) in gen._NOISE_SPEC.items():
        assert scale >= 1.8, (
            f"{series_type} 的测量噪声半幅只剩 {scale}mm，取整后日变化量会退化成一两种取值"
            "（真实台账实测：沉降 ±2.3mm、位移 σ=2.6mm）"
        )
        assert kind in ("uniform", "normal"), f"{series_type} 的分布类型不认识：{kind}"
    assert set(gen._NOISE_SPEC) == {"settlement", "displacement"}, "两个指标都必须标定"

    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "backend" / "services" / "data_generator_service.py").read_text(encoding="utf-8")
    assert "limit_rate / 4" not in src and "limit_rate/4" not in src, (
        "噪声幅度又被写成从控制速率折算的了"
    )


def test_generated_deltas_look_like_real_ledger(client):
    """日变化量要像真实台账：0 / ±1 / ±2 混着来，平均量级也要对。

    这是用户"感觉全是正负 1、正负 2"的回归守卫 —— 而且**故意用「没配预警规则」的项目**：
    兜底限值 (30, 5) 正是当初把幅度压到 1.25mm 的那条路径。
    """
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-02", "end": "2026-08-31", "seed": 42},
    )

    deltas = [abs(x["delta_mm"]) for x in _series(client, pt) if x["delta_mm"] is not None]
    n = len(deltas)
    assert n >= 25, f"样本太少（{n}）"
    avg = sum(deltas) / n
    assert 0.9 <= avg <= 2.0, (
        f"平均日变化量 {avg:.2f}mm 不在真实台账量级（实测 1.20mm）"
    )
    share_ge2 = sum(1 for d in deltas if d >= 2) / n
    assert share_ge2 >= 0.15, (
        f"|Δ|≥2 只占 {share_ge2:.0%}，又退回成'全是正负 1'（真实台账 38%）"
    )
    share_zero = sum(1 for d in deltas if d == 0) / n
    assert share_zero <= 0.50, f"零值占 {share_zero:.0%}，曲线被压平了"


def test_generated_cumulative_keeps_wandering(client):
    """累计量不能贴着初值不动 —— 真实台账 21 期跨度有 9~12mm。

    触发点是用户在界面上看到"累计变化量一直是 0/±1"，以为没生成成功。
    """
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-02", "end": "2026-08-31", "seed": 5},
    )
    series = _series(client, pt)
    cums = [x["cumulative_mm"] for x in series if x["cumulative_mm"] is not None]
    span = max(cums) - min(cums)
    assert span >= 5.0, (
        f"整段累计跨度只有 {span:.1f}mm，真实台账 21 期是 9~12mm（曲线被压平了）"
    )


def test_injection_old_small_noise_would_be_caught(client, monkeypatch):
    """注入回归自测：把噪声半幅改回旧做法（控制速率 ÷ 4 → 1.25mm），守卫必须报警。

    证明 `test_generated_deltas_look_like_real_ledger` 不是摆设 ——
    旧参数下 |Δ|≥2 的占比会掉到 15% 以下，正好踩中那条断言。
    """
    monkeypatch.setattr(gen, "_NOISE_SPEC",
                        {"settlement": ("uniform", 1.25), "displacement": ("uniform", 1.25)})
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)
    client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-02", "end": "2026-08-31", "seed": 42},
    )
    deltas = [abs(x["delta_mm"]) for x in _series(client, pt) if x["delta_mm"] is not None]
    n = len(deltas)
    share_ge2 = sum(1 for d in deltas if d >= 2) / n
    avg = sum(deltas) / n
    assert share_ge2 < 0.15 or avg < 0.9, (
        f"旧参数（±1.25mm）居然也通过了形态判据（|Δ|≥2 占 {share_ge2:.0%}、均值 {avg:.2f}mm）—— "
        "说明这条守卫抓不住问题，等于没写"
    )


def test_generated_points_differ_from_each_other(client):
    """不同测点的曲线不该雷同（每点每分量独立给参数）。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ-VAR", "name": "点间差异"}).json()
    client.put(
        f"/api/v1/projects/{p['id']}/warning-rules",
        json={"rules": [{"indicator": "settlement", "level": "control",
                         "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 2.0}]},
    )
    ids = []
    for no in ("JJC1", "JJC2", "JJC3"):
        pt = client.post(
            f"/api/v1/projects/{p['id']}/points",
            json={"point_no": no, "point_type": "settlement"},
        ).json()
        ids.append(pt["id"])
        _add_record(client, pt["id"], "2026-08-01", 5000.0)
    client.post(
        f"/api/v1/projects/{p['id']}/generate-data",
        params={"start": "2026-08-02", "end": "2026-08-25", "seed": 11},
    )

    finals = []
    for pid_pt in ids:
        s = _series(client, pid_pt)
        assert s, "该测点没有生成数据"
        finals.append(s[-1]["cumulative_mm"])
    assert len(set(finals)) > 1, f"三个测点的最终累计完全一样（{finals}）—— 曲线参数没有按点区分"


def test_warning_profile_of_generated_data_is_realistic(client):
    """生成数据触发预警的**比例**要像真实台账 —— 两头都不能过。

    2026-09-29 之前的规矩是"生成的数据一条预警都不出"，代价是位移被压成 0/±1/±2；
    用户明确"可以不用（规避）预警"之后放开了，于是这里改成盯**比例**：
    · 沉降（控制速率 10mm/d）：真实台账从没接近过 10mm/d，所以不该出现预警；
    · 位移（控制速率 3mm/d）：真实台账 |Δ|≥3 占 34%，所以必然有预警 ——
      但要是超过一半，就说明生成出了现场不会有的量级。
    """
    pid, pt = _setup(client, rate=10.0)
    _add_record(client, pt, "2026-07-31", 5000.0)
    warn_days = 0
    total_days = 0
    for seed in (1, 2, 3, 5, 8):
        client.post(
            f"/api/v1/projects/{pid}/generate-data",
            params={"start": "2026-08-01", "end": "2026-08-31", "seed": seed,
                    "on_conflict": "replace_all"},
        )
        series = _series(client, pt)
        warn_days += sum(1 for x in series if x["warning_level"] != "normal")
        total_days += len(series)
    share = warn_days / total_days
    assert share <= 0.10, (
        f"沉降（控制 10mm/d）有 {share:.0%} 的天数带预警 —— 真实台账从没到过这个量级"
    )


def test_generated_displacement_warns_like_real_ledger(client):
    """位移放开限幅后 **应该** 出现预警，比例在真实台账那个区间里。

    真实台账 |Δ|≥3 占 34%（控制速率 3mm/d → 这些都是预警）。
    这条既防"又被限幅压平"（比例过低），也防"生成出离谱数据"（比例过高）。
    """
    pid, pt = _setup(client, point_type="displacement", rate=10.0, disp_rate=3.0)
    _add_record(client, pt, "2026-07-31", 5000.0,
                series_type="displacement", component="X")
    warn_days = 0
    total_days = 0
    for seed in (1, 2, 3, 5, 8):
        client.post(
            f"/api/v1/projects/{pid}/generate-data",
            params={"start": "2026-08-01", "end": "2026-08-31", "seed": seed,
                    "on_conflict": "replace_all"},
        )
        series = _displacement_series(client, pt)
        warn_days += sum(1 for x in series if x["warning_level"] != "normal")
        total_days += len(series)
    share = warn_days / total_days
    assert 0.10 <= share <= 0.55, (
        f"位移有 {share:.0%} 的天数带预警，真实台账是 34% —— "
        "低于 10% 说明又被人为压平了，高于 55% 说明生成得离谱"
    )


def test_generation_reproducible_with_same_seed(client):
    """同 seed 必须得到同一条曲线（可复算，便于复现问题）。"""
    pid, pt = _setup(client)
    _add_record(client, pt, "2026-08-01", 5000.0)

    def run_and_read():
        client.post(
            f"/api/v1/projects/{pid}/generate-data",
            params={"start": "2026-08-02", "end": "2026-08-20", "seed": 4242,
                    "on_conflict": "replace_all"},
        )
        return [x["value_mm"] for x in _series(client, pt)]

    first = run_and_read()
    second = run_and_read()
    assert first == second, "同 seed 两次生成结果不同 —— 曲线不再是确定性的"


# ───────── 控制值缺失时不许凭空编一个限值（2026-10-14 改）─────────
# 原先 `_limits` 在没配规则时 `return 30.0, 5.0`。后果不只是"按错的数生成"：
# 生成器用控制值划定累计上限与单期防呆限幅，于是**一条规则都没配的项目**会得到
# 一批按从没配过的限值缩放出来的数据，而它会被拿去画曲线、导日报、进汇报材料。
# 用户当天定的口径：拦住，不让生成。
def test_generation_blocked_when_no_control_values(client):
    """一条控制值都没配 → 422，且库里一条记录都不许多。"""
    pid, pt = _setup(client, with_rules=False)
    _add_record(client, pt, "2026-08-01", 5000.0)

    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-08-02", "end": "2026-08-12", "seed": 7})

    assert r.status_code == 422, f"没配控制值却生成成功了：{r.status_code} {r.text}"
    detail = r.json()["detail"]
    # 报错必须把"去哪儿配"说清楚 —— 只说"参数错误"等于把人逼回猜
    assert "预警规则" in detail and "累计变化量" in detail, detail
    assert "30" not in detail, f"不该把兜底数字暴露成依据：{detail}"

    with SessionLocal() as db:
        n = db.query(ObservationRecord).filter(ObservationRecord.point_id == pt).count()
    assert n == 1, f"被拦住了却仍写进库：现有 {n} 条（只该有那 1 条手工初始值）"


def test_only_the_series_that_will_be_generated_is_validated(client):
    """只测沉降的基坑，不该因为没配「水平位移」规则被拦。"""
    pid, pt = _setup(client, point_type="settlement", with_rules=True)
    _add_record(client, pt, "2026-08-01", 5000.0)

    r = client.post(f"/api/v1/projects/{pid}/generate-data",
                    params={"start": "2026-08-02", "end": "2026-08-10", "seed": 3})

    assert r.status_code == 200, r.text
    assert r.json()["generated"] > 0


def test_configured_control_value_actually_drives_the_bound(client):
    """配了累计 8 就必须按 8 收口。

    旧写法 `if rule and cum and rate` 会把**只填了累计**的半套规则整对丢弃、换成 30 ——
    于是"我明明填了 8"生成的数据却能爬到 28。这里用同种子跑两遍对比：
    上限 8 的那一轮必须明显比上限 30 的那一轮收得更紧，否则说明配置的数字没被用上。
    """
    pid, pt = _setup(client, point_type="settlement", with_rules=False)
    _add_record(client, pt, "2026-08-01", 5000.0)

    def put_rules(cum: float | None, rate: float | None) -> None:
        client.put(
            f"/api/v1/projects/{pid}/warning-rules",
            json={"rules": [{"indicator": "settlement", "level": "control",
                             "threshold_cumulative_mm": cum,
                             "threshold_rate_mm_per_day": rate}]},
        )

    def run() -> float:
        r = client.post(f"/api/v1/projects/{pid}/generate-data",
                        params={"start": "2026-08-02", "end": "2026-09-10",
                                "seed": 99, "on_conflict": "replace_all"})
        assert r.status_code == 200, r.text
        cums = [abs(x["cumulative_mm"]) for x in _series(client, pt)
                if x["cumulative_mm"] is not None]
        return max(cums)

    put_rules(8.0, None)          # 半套：只填累计
    peak_tight = run()
    # cum_cap = 8×0.95 = 7.6，而落库前是 `round(cand)` 取整到整毫米 → 实测峰值就是 8.0。
    # 所以容差按"取整 ±1mm"给，不能按 7.6 卡；重点是它**远低于**旧兜底 30 允许的 28.5。
    assert peak_tight <= round(8.0 * 0.95) + 1.0, (
        f"累计控制值填 8，生成数据却达到 {peak_tight:.1f}mm —— 填进去的数没生效"
    )

    put_rules(30.0, None)         # 同一批点、同一种子，只把上限放宽
    peak_loose = run()
    assert peak_loose > peak_tight, (
        f"放宽控制值后峰值没变（{peak_tight:.1f} vs {peak_loose:.1f}）"
        " —— 说明累计上限是写死的，与项目配置无关"
    )


def test_missing_cumulative_leaves_that_dimension_unbounded():
    """没配累计 ⇒ cum_cap=inf（这一维不设限），而不是拿 30 去"保护"合规。

    速率缺失时防呆限幅改锚该系列自己的日波动量级：量级要与配了速率时同档，
    所以这里断言的是"仍在合理范围内"，不是"没有上限"。
    """
    plan = gen._walk_plan(None, 2.0, 0.0, 2.3)
    assert plan["cum_cap"] == float("inf")

    # 累计与速率都缺时不该走到这里（_require_limits 已拦），
    # 但纯函数本身也要能站住：防呆上限由量级给出，不能是 inf 或 0
    plan2 = gen._walk_plan(None, None, 0.0, 2.6)
    assert 3.0 <= plan2["ceiling"] <= 12.0, plan2


# ──────────────── 精度跟随项目台账（用户 2026-10-16） ────────────────


def test_generated_precision_follows_project_ledger(client):
    """生成值的精度跟**项目自己的记录习惯**走。

    用户 2026-10-16：
        「第一期变化值会出现像小数点，后面所有期都是整数在变化了，你这很容易被看出来」

    实测生产库：十一号/十二号的台账记到 0.1mm（2651.3、2798.6），八号那几个记到 1mm（4907）。
    生成器原先一律 `round(cand)` 砍成整数毫米 —— 真实段 48% 带小数、生成段 0%，
    同一列数字摆在一起，一眼就看出哪一段是造的。

    ⚠ 判据用"带小数的读数占比"，不是"必须每一位都带小数"：
    真实台账本身就是混的（同一个项目里整数和 0.1mm 都有）。
    """
    pid, pt = _setup(client)
    for d, v in (("2026-07-29", 5000.4), ("2026-07-30", 5001.1), ("2026-07-31", 5000.7)):
        _add_record(client, pt, d, v)

    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-25", "seed": 7},
    )
    assert r.status_code == 200, r.text

    gen = [x.value_mm for x in _rows(pt) if x.source == "generated"]
    assert gen, "一条都没生成"
    frac = sum(1 for v in gen if abs(v - round(v)) > 1e-9) / len(gen)
    assert frac > 0.2, (
        f"生成的读数只有 {frac:.0%} 带小数，而这个项目的台账记到 0.1mm —— "
        "真实段带小数、生成段整整齐齐全整数，一眼就看出哪一段是造的"
    )


def test_generated_stays_integer_for_integer_ledger(client):
    """台账记到 1mm 的项目 → 生成值仍是整数（别把本来对的项目改坏）。"""
    pid, pt = _setup(client)
    for d, v in (("2026-07-30", 5000.0), ("2026-07-31", 5001.0)):
        _add_record(client, pt, d, v)

    client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-20", "seed": 7},
    )
    gen = [x.value_mm for x in _rows(pt) if x.source == "generated"]
    bad = [v for v in gen if abs(v - round(v)) > 1e-9]
    assert gen and not bad, f"整数台账的项目被生成出了小数：{bad[:5]}"


def test_new_project_without_ledger_still_uses_integer(client):
    """一条真实历史都没有的项目（只有初值）→ 沿用 1mm。

    这条防的是"为了修上面那个问题，把全新项目也改成带小数"。
    ⚠ 测点必须带 `initial_value_mm`：初值也没有的话，生成器一条都不生成
    （那是既有行为，不是这里要测的东西）。
    """
    p = client.post("/api/v1/projects", json={"code": "PRJ-NEW", "name": "全新项目"}).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 5000.0},
    ).json()
    client.put(
        f"/api/v1/projects/{p['id']}/warning-rules",
        json={"rules": [
            {"indicator": "settlement", "level": "control",
             "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 2.0},
            {"indicator": "settlement", "level": "alarm",
             "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 4.0},
        ], "source_note": "测试规范"},
    )
    client.post(
        f"/api/v1/projects/{p['id']}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-15", "seed": 7},
    )
    gen = [x.value_mm for x in _rows(pt["id"]) if x.source == "generated"]
    bad = [v for v in gen if abs(v - round(v)) > 1e-9]
    assert gen and not bad, f"没有台账参照的项目不该自己造出小数：{bad[:5]}"


def test_displacement_precision_also_follows_ledger(client):
    """水平位移跟沉降一样要跟随台账 —— 用户 2026-10-16：「不要只改沉降观察」。

    实测生产库：项目 11/12 的位移真实数据本身就是混的
    （X：整数 13 条 / 0.1mm 6 条 / 0.01mm 7 条），所以**按"众数"取精度会落回 0 位**，
    "生成段全整数"的老毛病在位移上原样复现。要按分布抽。
    """
    pid, pt = _setup(client, point_type="displacement", disp_rate=1.0)
    for d, x, y in (
        ("2026-07-29", 3413111727.2, 510110587.2),
        ("2026-07-30", 3413115193.9, 510110590.4),
        ("2026-07-31", 3413115193.4, 510110590.9),
    ):
        _add_record(client, pt, d, x, series_type="displacement", component="X")
        _add_record(client, pt, d, y, series_type="displacement", component="Y")

    r = client.post(
        f"/api/v1/projects/{pid}/generate-data",
        params={"start": "2026-08-01", "end": "2026-08-25", "seed": 7},
    )
    assert r.status_code == 200, r.text

    gen = [x for x in _rows(pt) if x.source == "generated" and x.series_type == "displacement"]
    assert gen, "位移一条都没生成"
    for comp in ("X", "Y"):
        vals = [x.value_mm for x in gen if x.component == comp]
        assert vals, f"位移 {comp} 一条都没生成"
        frac = sum(1 for v in vals if abs(v - round(v)) > 1e-9) / len(vals)
        assert frac > 0.2, (
            f"位移 {comp} 的生成值只有 {frac:.0%} 带小数 —— 而这个项目的位移台账记到 0.1/0.01mm，"
            "真实段带小数、生成段整整齐齐，一眼就看出哪一段是造的"
        )
