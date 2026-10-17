# -*- coding: utf-8 -*-
"""测试：预警规则库 —— 规则独立于项目，由规则声明作用于哪些项目。

用户 2026-10-09：「把预警规则提出来 直接作用于系统 用预警规则来选择项目」

改之前：`warning_rules.project_id` 把规则钉死在单个项目上 —— 实测生产库
**8 个项目的规则是同一套数各抄了一遍（32 行）**，另 4 个项目一条规则都没有（不判预警）。
改之后：一套规则（各指标 × 各等级）自己声明作用于哪些项目，改一次全体跟随。

⚠ 读规则的地方（算预警/逐期对比/生成数据/项目结论/预警事件/报表控制值）**全部只经过
`effective_rules()`** —— 它的签名与返回结构刻意保持不变，本文件顺带守住这一点。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from backend.core.db import engine, migrate_warning_rule_sets
from backend.services import warning_service

SETTLE = "竖向位移"

_RULES = [
    {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
    {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
    {"indicator": "displacement", "level": "control", "threshold_cumulative_mm": 20, "threshold_rate_mm_per_day": 3},
    {"indicator": "displacement", "level": "alarm", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 4},
]


def _rules_with(settle_control_cum: float) -> list[dict]:
    """把沉降「控制值·累计」换成指定值，**报警值同步抬到不低于它**。

    报警值低于控制值是废配置（一超限直接落「报警」，「预警」这一级永远出不来），
    2026-10-16 起保存时被校验拒收 → 放宽控制值必须连报警值一起放宽。
    """
    out = [dict(r) for r in _RULES]
    out[0]["threshold_cumulative_mm"] = settle_control_cum
    out[1]["threshold_cumulative_mm"] = max(40, settle_control_cum)
    return out


# ─────────────────────────── 脚手架 ───────────────────────────


def _mk_project(client, code: str, name: str) -> int:
    return client.post("/api/v1/projects", json={"code": code, "name": name}).json()["id"]


def _mk_project_with_data(client, code: str, name: str, values=(("2026-09-01", 3500.0), ("2026-09-30", 3530.0))) -> int:
    """建项目 + 1 个测点 + 两期数据。

    跨 29 天累计 +30mm → **累计到 30、速率只有约 1mm/d**，这样才能单独验证"累计限值"
    那一条（速率限值是 10/12，若数据一天涨 30mm 会直接把报警顶出来，测不出累计口径）。
    """
    pid = _mk_project(client, code, name)
    pt = client.post(
        f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "settlement"}
    ).json()
    for d, v in values:
        client.post(
            f"/api/v1/points/{pt['id']}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
        )
    return pid


def _create_set(client, name: str, rules=None, project_ids=(), is_global=False):
    return client.post(
        "/api/v1/warning-rule-sets",
        json={
            "name": name,
            "rules": rules or _RULES,
            "source_note": "测试方案",
            "project_ids": list(project_ids),
            "is_global": is_global,
        },
    )


def _effective(client, pid: int) -> dict[str, list]:
    rows = client.get(f"/api/v1/projects/{pid}/warning-rules").json()
    return {f"{r['indicator']}/{r['level']}": [r["threshold_cumulative_mm"], r["threshold_rate_mm_per_day"]] for r in rows}


def _warning_levels(client, pid: int) -> list[str | None]:
    """该项目第一个测点的沉降计算序列上的预警等级（走 /points/{id}/computed）。"""
    pts = client.get(f"/api/v1/projects/{pid}/points").json()
    pt = pts[0]
    rows = client.get(
        f"/api/v1/points/{pt['id']}/computed",
        params={"series_type": "settlement", "component": "value"},
    ).json()
    return [r.get("warning_level") for r in rows]


# ─────────────────────── 1. 规则独立 + 由规则选项目 ───────────────────────


def test_new_project_has_no_rules_until_bound(client):
    """新项目没有任何规则 → 生效规则为空（不判预警），不是偷偷套别人的数。"""
    pid = _mk_project(client, "A", "甲基坑")
    assert _effective(client, pid) == {}
    assert client.get(f"/api/v1/projects/{pid}/warning-rules/set").json() == {
        "set_id": None, "name": None, "is_global": False, "shared_project_count": 0
    }


def test_one_set_can_cover_several_projects(client):
    """**核心**：一套规则勾住多个项目，这些项目立刻按它判定。"""
    a = _mk_project(client, "A", "甲基坑")
    b = _mk_project(client, "B", "乙基坑")
    r = _create_set(client, "基坑通用方案", project_ids=[a, b])
    assert r.status_code == 200, r.text
    assert sorted(r.json()["project_ids"]) == [a, b]
    assert r.json()["project_names"] == ["甲基坑", "乙基坑"]

    for pid in (a, b):
        eff = _effective(client, pid)
        assert eff["settlement/control"] == [30, 10]
        assert eff["displacement/alarm"] == [30, 4]


def test_editing_set_applies_to_all_bound_projects(client):
    """改一套规则 → 所有绑定它的项目**一起变**（这就是"不用每个项目各配一遍"）。"""
    a = _mk_project(client, "A", "甲基坑")
    b = _mk_project(client, "B", "乙基坑")
    c = _mk_project(client, "C", "丙基坑")
    sid = _create_set(client, "共用方案", project_ids=[a, b]).json()["id"]
    _create_set(client, "丙自己的", rules=_rules_with(15), project_ids=[c])

    r = client.put(f"/api/v1/warning-rule-sets/{sid}", json={"rules": _rules_with(25)})
    assert r.status_code == 200, r.text

    assert _effective(client, a)["settlement/control"] == [25, 10]
    assert _effective(client, b)["settlement/control"] == [25, 10]
    assert _effective(client, c)["settlement/control"] == [15, 10], "没绑这套的项目不该被改到"


def test_global_set_is_fallback_only(client):
    """通用规则只兜底：没绑规则的项目用它，绑了自己那套的项目仍用自己的。"""
    a = _mk_project(client, "A", "甲基坑")
    b = _mk_project(client, "B", "乙基坑")
    _create_set(client, "兜底通用", rules=_rules_with(99), is_global=True)
    _create_set(client, "甲自己的", rules=_rules_with(11), project_ids=[a])

    assert _effective(client, b)["settlement/control"] == [99, 10], "没配规则的项目应由通用兜底"
    assert _effective(client, a)["settlement/control"] == [11, 10], "自己的规则优先于通用"


def test_project_belongs_to_at_most_one_set(client):
    """一个项目最多绑一套：绑到新套时自动从旧套上解绑（不会同时吃两套限值）。"""
    a = _mk_project(client, "A", "甲基坑")
    s1 = _create_set(client, "旧方案", rules=_rules_with(10), project_ids=[a]).json()["id"]
    s2 = _create_set(client, "新方案", rules=_rules_with(50), project_ids=[a]).json()["id"]

    assert _effective(client, a)["settlement/control"] == [50, 10]
    sets = {s["id"]: s["project_ids"] for s in client.get("/api/v1/warning-rule-sets").json()}
    assert sets[s1] == [], "旧套应已解绑"
    assert sets[s2] == [a]


def test_set_name_must_be_unique(client):
    a = _mk_project(client, "A", "甲基坑")
    _create_set(client, "同名方案", project_ids=[a])
    r = _create_set(client, "同名方案", project_ids=[a])
    assert r.status_code == 400
    assert "换个名字" in r.json()["detail"]


def test_delete_set_unbinds_projects(client):
    a = _mk_project(client, "A", "甲基坑")
    sid = _create_set(client, "临时方案", project_ids=[a]).json()["id"]
    assert client.delete(f"/api/v1/warning-rule-sets/{sid}").status_code == 200
    assert _effective(client, a) == {}
    assert client.get(f"/api/v1/projects/{a}/warning-rules/set").json()["set_id"] is None


# ─────────────────────── 2. 从项目侧改：共用套自动分叉 ───────────────────────


def test_project_side_edit_forks_shared_set(client):
    """共用规则被一个项目改时**必须分叉** —— 否则改一个基坑的限值会连带改掉别的基坑。"""
    a = _mk_project(client, "A", "甲基坑")
    b = _mk_project(client, "B", "乙基坑")
    sid = _create_set(client, "共用方案", project_ids=[a, b]).json()["id"]

    r = client.put(f"/api/v1/projects/{a}/warning-rules", json={"rules": _rules_with(12), "source_note": "甲改了"})
    assert r.status_code == 200, r.text

    assert _effective(client, a)["settlement/control"] == [12, 10], "甲应已换成自己的新值"
    assert _effective(client, b)["settlement/control"] == [30, 10], "乙的限值必须原封不动"
    sets = client.get("/api/v1/warning-rule-sets").json()
    shared = next(s for s in sets if s["id"] == sid)
    assert shared["project_ids"] == [b], "原共用套应只剩乙"


def test_project_side_edit_on_own_set_updates_in_place(client):
    """只绑这一个项目的规则 → 就地改（不必新建一套，避免规则越攒越多）。"""
    a = _mk_project(client, "A", "甲基坑")
    sid = _create_set(client, "甲自己的", project_ids=[a]).json()["id"]
    client.put(f"/api/v1/projects/{a}/warning-rules", json={"rules": _rules_with(18), "source_note": "改"})
    assert _effective(client, a)["settlement/control"] == [18, 10]
    sets = client.get("/api/v1/warning-rule-sets").json()
    assert [s["id"] for s in sets] == [sid], "不该多出一套"


def test_project_without_set_creates_one_on_edit(client):
    """没规则的旧项目，在项目里配一次规则 → 自动为它建一套并绑定（老用法不会失效）。"""
    a = _mk_project(client, "A", "甲基坑")
    r = client.put(f"/api/v1/projects/{a}/warning-rules", json={"rules": _RULES, "source_note": "手配"})
    assert r.status_code == 200, r.text
    info = client.get(f"/api/v1/projects/{a}/warning-rules/set").json()
    assert info["set_id"] is not None and info["shared_project_count"] == 1
    assert _effective(client, a)["settlement/alarm"] == [40, 12]


def test_project_set_info_reports_sharing(client):
    """界面要靠这个接口提示"这套还被 N 个项目用着"。"""
    a = _mk_project(client, "A", "甲基坑")
    b = _mk_project(client, "B", "乙基坑")
    _create_set(client, "共用", project_ids=[a, b])
    info = client.get(f"/api/v1/projects/{a}/warning-rules/set").json()
    assert info["shared_project_count"] == 2
    assert info["name"] == "共用"


# ─────────────────────── 3. 判定真的跟着规则走（端到端） ───────────────────────


def test_warning_judgement_follows_the_set(client):
    """**行为级验证**：同一份数据，换一套限值 → 预警等级随之改变。"""
    pid = _mk_project_with_data(client, "A", "甲基坑")  # 第2期累计 +30mm
    sid = _create_set(client, "宽限值", rules=_rules_with(50), project_ids=[pid]).json()["id"]

    levels = _warning_levels(client, pid)
    assert levels[-1] is None or levels[-1] == "normal", f"限值 50 时不该报警：{levels}"

    client.put(f"/api/v1/warning-rule-sets/{sid}", json={"rules": _rules_with(20)})
    levels = _warning_levels(client, pid)
    assert levels[-1] == "control", f"限值收到 20 后累计 30mm 应判预警：{levels}"


def test_report_control_values_follow_the_set(client):
    """报表「控制值」两列的唯一出处就是这套规则 —— 换规则，报表跟着变。"""
    from backend.core.db import SessionLocal

    pid = _mk_project_with_data(client, "A", "甲基坑")
    sid = _create_set(client, "报表方案", rules=_rules_with(35), project_ids=[pid]).json()["id"]
    client.put(f"/api/v1/warning-rule-sets/{sid}", json={"rules": _rules_with(28)})

    with SessionLocal() as db:
        assert warning_service.report_control_values(db, pid, "settlement") == (28, 10)


# ─────────────────────── 4. 迁移 ───────────────────────


def _insert_legacy_project_rules(pid: int, cum: float) -> None:
    """直接写库造"老形态"规则（project_id 非空、不属于任何套）。"""
    with engine.begin() as conn:
        for r in _rules_with(cum):
            conn.execute(
                text(
                    "INSERT INTO warning_rules (project_id, set_id, indicator, level, "
                    "threshold_cumulative_mm, threshold_rate_mm_per_day, is_active, source_note) "
                    "VALUES (:pid, NULL, :ind, :lvl, :cum, :rate, 1, '老方案')"
                ),
                {"pid": pid, "ind": r["indicator"], "lvl": r["level"],
                 "cum": r["threshold_cumulative_mm"], "rate": r["threshold_rate_mm_per_day"]},
            )


def test_migration_groups_and_merges_identical_projects(client):
    """老规则按项目分组成套；**数值完全相同的项目合并成一套**（实测正是这种情况）。"""
    a = _mk_project(client, "A", "甲基坑")
    b = _mk_project(client, "B", "乙基坑")
    c = _mk_project(client, "C", "丙基坑")
    _insert_legacy_project_rules(a, 30)
    _insert_legacy_project_rules(b, 30)   # 与 A 完全相同
    _insert_legacy_project_rules(c, 12)   # 与 A/B 不同

    stats = migrate_warning_rule_sets()
    assert stats["bound_projects"] == 3

    sets = client.get("/api/v1/warning-rule-sets").json()
    assert len(sets) == 2, f"应合并成 2 套（A/B 一套 + C 一套），实际 {len(sets)}"
    for s in sets:
        assert len(s["rules"]) == 4, f"每套就该 4 条规则，实际 {len(s['rules'])}（合并时不能堆重复行）"

    shared = next(s for s in sets if set(s["project_ids"]) == {a, b})
    assert _effective(client, a)["settlement/control"] == [30, 10]
    assert _effective(client, b)["settlement/control"] == [30, 10]
    assert _effective(client, c)["settlement/control"] == [12, 10]


def test_migration_is_idempotent_and_keeps_effective_rules(client):
    """迁移可重复执行；且**迁移前后生效规则逐字一致**（限值一个字都不能变）。"""
    a = _mk_project(client, "A", "甲基坑")
    _insert_legacy_project_rules(a, 27)
    before = _effective(client, a)

    first = migrate_warning_rule_sets()
    second = migrate_warning_rule_sets()
    assert first["bound_projects"] == 1
    assert second["bound_projects"] == 0, "重复执行又建了一套"

    assert _effective(client, a) == before, "迁移改了限值 —— 这是绝对不能发生的"


# ─────────────────────── 5. 硬删项目 ───────────────────────


def test_hard_delete_keeps_shared_rules(client):
    """共用规则不跟着项目走：删掉一个项目只是解绑。"""
    a = _mk_project_with_data(client, "A", "甲基坑")
    b = _mk_project_with_data(client, "B", "乙基坑")
    sid = _create_set(client, "共用方案", project_ids=[a, b]).json()["id"]

    r = client.delete(f"/api/v1/projects/{a}/hard", params={"force": "A"})
    assert r.status_code == 200, r.text
    assert r.json()["rules_kept"] == ["共用方案"]

    assert _effective(client, b)["settlement/control"] == [30, 10]
    sets = {s["id"]: s for s in client.get("/api/v1/warning-rule-sets").json()}
    assert sid in sets and sets[sid]["project_ids"] == [b]


def test_hard_delete_removes_exclusive_rules(client):
    """只被这一个项目用的规则 → 跟着删（不留孤儿套）。"""
    a = _mk_project_with_data(client, "A", "甲基坑")
    sid = _create_set(client, "甲独占", project_ids=[a]).json()["id"]
    r = client.delete(f"/api/v1/projects/{a}/hard", params={"force": "A"})
    assert r.status_code == 200
    assert r.json()["rules_kept"] == []
    assert all(s["id"] != sid for s in client.get("/api/v1/warning-rule-sets").json())


# ─────────────────────── 6. 守卫 ───────────────────────

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


def test_effective_rules_signature_is_stable():
    """`effective_rules(db, project_id)` 是"生效规则"的唯一出处，6 个调用方都靠它。

    上面这条守卫锁住签名；这里再锁住"那几个调用方仍然经过它"——
    一旦有人在别处直接查 warning_rules 表判预警，规则库改造就会漏改那一处。

    2026-10-14：调用方现在可以通过 `warning_service` 的两个出口间接读
    （`rule_limits` = 判定用的六阈值（累计/ΔV/速率 × 控制/报警）、
    `report_control_values` = 报表控制值两列）。
    出口仍然只有一个文件，所以这条守卫要断的是**不许绕过 warning_service**，
    而不是"每个文件都得亲手写 effective_rules"。
    """
    import inspect

    from backend.services import warning_service as ws

    sig = inspect.signature(ws.effective_rules)
    assert list(sig.parameters) == ["db", "project_id"], "签名变了会波及 6 个调用方"

    backend = SRC.parent.parent / "backend"
    consumers = {
        "services/calc_service.py": "算预警等级",
        "services/comparison_service.py": "逐期对比",
        "services/data_generator_service.py": "生成数据",
        "services/project_brief_service.py": "项目一句话结论",
        "services/warning_event_service.py": "预警事件",
        "services/warning_service.py": "报表控制值",
    }
    gates = ("effective_rules", "rule_limits", "report_control_values")
    for rel, what in consumers.items():
        text = (backend / rel).read_text(encoding="utf-8")
        assert any(g in text for g in gates), f"{rel}（{what}）不再经过 warning_service 读规则"
        # 直接查表 = 第二套出处（会漏掉规则库的绑定/通用/遗留四层解析）
        assert "WarningRule)" not in text or rel == "services/warning_service.py", (
            f"{rel} 自己查了 warning_rules 表"
        )


def test_frontend_exposes_scope_picker() -> None:
    page = (SRC / "components" / "WarningRulesModal.tsx").read_text(encoding="utf-8")
    assert "所有项目通用" in page, "没有通用开关"
    assert "作用于哪些项目" in page, "没说明适用范围"
    assert "setSetProjects" in page and "createSet" in page
    assert "一起生效" in page, "共用规则改限值的影响没提示出来"


def test_capability_flag_declared_both_sides() -> None:
    from backend.main import BACKEND_FEATURES

    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert "warning_rule_library" in BACKEND_FEATURES
    assert "warning_rule_library" in app


# ─────────────── 7. 「本次变化量 ΔV」这一档（2026-10-16） ───────────────
#
# 用户原话：「报警值和预警值都是来控制变化速率的 我想的一个是控制系统每期变化的值的上限」。
# 落地口径：**判定三维 = 累计 / 本次变化量 ΔV / 速率**，各判一次取最严；ΔV 只看这一期跳了多少，
# 不被观测间隔摊薄。**报表上印的仍是累计 + 速率两格**（ΔV 不上表）—— 用户定的是
# "表里印的数 = 系统判预警的数"，多印一格反而多出一个对不上号的数。

_RULES_WITH_DELTA = [
    {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 40,
     "threshold_delta_mm": 20, "threshold_rate_mm_per_day": 3},
    {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 50,
     "threshold_delta_mm": 35, "threshold_rate_mm_per_day": 4},
    {"indicator": "displacement", "level": "control", "threshold_cumulative_mm": 20,
     "threshold_delta_mm": None, "threshold_rate_mm_per_day": 3},
    {"indicator": "displacement", "level": "alarm", "threshold_cumulative_mm": 30,
     "threshold_delta_mm": None, "threshold_rate_mm_per_day": 4},
]


def _delta_of(client, pid: int) -> dict[str, float | None]:
    rows = client.get(f"/api/v1/projects/{pid}/warning-rules").json()
    return {f"{r['indicator']}/{r['level']}": r["threshold_delta_mm"] for r in rows}


def test_delta_threshold_round_trips(client):
    """ΔV 存得进、读得出；没填的那一维读回来就是 null（= 不判，不当 0）。"""
    pid = _mk_project(client, "A", "甲基坑")
    sid = _create_set(client, "带ΔV的方案", rules=_RULES_WITH_DELTA, project_ids=[pid]).json()["id"]

    d = _delta_of(client, pid)
    assert d["settlement/control"] == 20 and d["settlement/alarm"] == 35
    assert d["displacement/control"] is None, "没填 ΔV 不许补 0"

    r = client.put(f"/api/v1/warning-rule-sets/{sid}", json={"rules": _RULES})
    assert r.status_code == 200, r.text
    assert _delta_of(client, pid)["settlement/control"] is None, "改回不填 ΔV 的规则后该清零"


def test_delta_limit_judges_where_rate_is_diluted(client):
    """**行为级**：同一份数据，只因为填了 ΔV 才判出预警。

    数据是相隔 29 天累计 +30mm → 速率只有约 1mm/d（限 3）、累计 30（限 40）都不超；
    但这一期实实在在跳了 30mm，ΔV 控制值 20 → 判预警。
    """
    pid = _mk_project_with_data(client, "A", "甲基坑")
    _create_set(client, "带ΔV的方案", rules=_RULES_WITH_DELTA, project_ids=[pid])
    assert _warning_levels(client, pid)[-1] == "control", "ΔV 该把这期顶成预警"

    # 把 ΔV 撤掉（只留累计/速率）→ 同一份数据立刻变回正常，证明判级真的来自这一维
    sid = client.get(f"/api/v1/projects/{pid}/warning-rules/set").json()["set_id"]
    loose = [dict(r) for r in _RULES_WITH_DELTA]
    for r in loose:
        r["threshold_delta_mm"] = None
    client.put(f"/api/v1/warning-rule-sets/{sid}", json={"rules": loose})
    assert _warning_levels(client, pid)[-1] in (None, "normal")


def test_delta_only_rule_still_blocks_export(client):
    """只填 ΔV、累计/速率两格都空 → 导出照样拦 422。

    ΔV 不上报表，所以它**不算**"这个项目配了控制值"：拦不拦的口径不能因为它而变，
    否则报表上会出现一栏没有出处的控制值，而判定用的那一格反倒没人看见。
    """
    pid = _mk_project_with_data(client, "A", "甲基坑")
    only_delta = [
        {"indicator": "settlement", "level": "control", "threshold_delta_mm": 20},
        {"indicator": "settlement", "level": "alarm", "threshold_delta_mm": 35},
    ]
    _create_set(client, "只有ΔV的方案", rules=only_delta, project_ids=[pid])
    r = client.get(f"/api/v1/projects/{pid}/reports/excel")
    assert r.status_code == 422, r.text
    assert "控制值" in r.json()["detail"]


def test_inverted_limits_rejected_with_a_readable_message(client):
    """报警值 < 控制值 → 三个入口一律 422，并且把话说清楚（**不是 500**）。

    500 是本条测试真正在防的东西：`WarningRuleBatchWrite` 是在端点函数体**里面**构造的，
    FastAPI 的请求校验接不到它，ValidationError 冒到顶就成了一片天书 —— 用户必须读到
    "报警值 20 < 控制值 30"，而不是 "Internal Server Error"。
    """
    pid = _mk_project(client, "A", "甲基坑")
    bad = [dict(r) for r in _RULES]
    bad[0]["threshold_cumulative_mm"] = 60  # 控制 60 > 报警 40

    r = _create_set(client, "倒挂方案", rules=bad, project_ids=[pid])
    assert r.status_code == 422, f"新建套应 422，实际 {r.status_code}：{r.text[:200]}"
    assert "报警值不能低于控制值" in r.json()["detail"]
    assert "累计变化量" in r.json()["detail"] and "60" in r.json()["detail"]

    good = _create_set(client, "正常方案", project_ids=[pid]).json()["id"]
    r = client.put(f"/api/v1/warning-rule-sets/{good}", json={"rules": bad})
    assert r.status_code == 422, r.text
    assert "报警值不能低于控制值" in r.json()["detail"]
    assert _effective(client, pid)["settlement/control"] == [30, 10], "被拒的这次不许已经写进库"

    r = client.put(f"/api/v1/projects/{pid}/warning-rules", json={"rules": bad})
    assert r.status_code == 422, r.text


def test_delta_column_added_without_inventing_thresholds(client):
    """迁移**只加列、不回填**：老规则这一维读到的是 NULL（= 不判），不是某个"合理默认值"。

    给老库统一填一个数 = 替每个基坑编阈值，这正是 2026-10-14 生成数据兜底那件事的毛病。
    顺带确认迁移幂等（重复 ALTER 会直接抛错、把启动弄崩，2026-10-09 踩过）。
    """
    from backend.core.db import migrate_rule_delta_limit

    pid = _mk_project(client, "A", "甲基坑")
    _insert_legacy_project_rules(pid, 30)  # 直接写库，绕开 ORM → 模拟"这列刚加上的老行"

    assert migrate_rule_delta_limit() == {"added_column": False}, "列已存在时不该再 ALTER"
    assert migrate_rule_delta_limit() == {"added_column": False}, "必须可重复执行"

    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(warning_rules)").fetchall()}
        nulls = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM warning_rules WHERE threshold_delta_mm IS NULL"
        ).fetchone()[0]
    assert "threshold_delta_mm" in cols
    assert int(nulls) >= 4, f"老规则的 ΔV 必须留空，实际有值：{nulls}"


def test_delta_migration_registered_in_init_db() -> None:
    """迁移必须挂在 `init_db()` 里，否则免安装包/老库升级后读这一列直接报错。"""
    src = (Path(__file__).resolve().parent.parent / "backend" / "core" / "db.py").read_text(
        encoding="utf-8"
    )
    assert "migrate_rule_delta_limit()" in src.split("def init_db", 1)[1], (
        "migrate_rule_delta_limit 没被 init_db 调用"
    )


def test_frontend_shows_the_third_limit() -> None:
    """界面上第三格必须存在，而且要说清它**不上报表**（口径不一致就又是一次"表和系统两个数"）。"""
    page = (SRC / "components" / "WarningRulesModal.tsx").read_text(encoding="utf-8")
    assert "本次变化量限值(mm)" in page and "threshold_delta_mm" in page, "弹窗里没有 ΔV 这一格"
    assert "只参与判定" in page, "没说明 ΔV 不印到报表上"
    assert "报警值比控制值还低" in page, "倒挂在界面上没有提示（只等后端 422）"

    events = (SRC / "pages" / "WarningEventsPage.tsx").read_text(encoding="utf-8")
    assert "'delta'" in events or "delta:" in events, "预警事件页不认得 delta 触发项"
    assert "本次变化量" in events, "预警事件页里 ΔV 那一档显示成英文/速率"

    api = (SRC / "api" / "client.ts").read_text(encoding="utf-8")
    assert "threshold_delta_mm" in api, "出入参类型没跟上，界面填的数会被当成多余字段丢掉"
    assert "control_sync_error" in api, "导入页读不到「控制值没同步成功」的原因"
