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
    out = [dict(r) for r in _RULES]
    out[0]["threshold_cumulative_mm"] = settle_control_cum
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
    """
    import inspect

    from backend.services import warning_service as ws

    sig = inspect.signature(ws.effective_rules)
    assert list(sig.parameters) == ["db", "project_id"], "签名变了会波及 6 个调用方"

    backend = SRC.parent.parent / "backend"
    consumers = [
        "services/calc_service.py",              # 算预警等级
        "services/comparison_service.py",        # 逐期对比
        "services/data_generator_service.py",    # 生成数据
        "services/project_brief_service.py",     # 项目一句话结论
        "services/warning_event_service.py",     # 预警事件
        "services/warning_service.py",           # 报表控制值
    ]
    missing = [c for c in consumers if "effective_rules" not in (backend / c).read_text(encoding="utf-8")]
    assert not missing, f"这些文件不再走 effective_rules（规则可能有两套出处）：{missing}"


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
