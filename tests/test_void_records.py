# -*- coding: utf-8 -*-
"""作废（void）记录的可见性与参与范围（2026-09-29 用户实测报障后补）。

## 事故经过

用户做完「替换」（把真实数据作废、写入新的演示数据）后反馈：
**「已经有数据了，但界面上没显示」** —— 记录页的输入框全空，而「录入状态」列写着"已录入"。

根因：同一天同一分量有**两条**记录（作废的真实数据 + 新的演示数据），
前端用 `find()` 取**第一条**，作废那条 id 更小排在前面 → 拿到空值 → 输入框空白；
而状态列取的是"最坏等级"（normal 高于 void）→ 显示"已录入"。**两处判据不一致。**

## 更严重的一层

`observation_repo.list_by_point(active_only=True)` 当时**只挡了 `superseded`，漏了 `void`**，
而这个参数被 13 处调用（计算引擎 / 预警 / 日报 / Excel 导出 / 逐期对比 / 项目简报…）——
作废数据会混进计算与报表。`get_active_by_key` 同样漏了，导致**作废后无法补录**
（被误判成"已有生效记录，禁止重复录入"）。

本文件盯住这四件事，任何一条回归都会当场失败。
"""
from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


def _setup(client, point_type: str = "settlement") -> tuple[int, int]:
    p = client.post("/api/v1/projects", json={"code": "PRJ-VOID", "name": "作废可见性"}).json()
    pt = client.post(
        f"/api/v1/projects/{p['id']}/points",
        json={"point_no": "JJC1", "point_type": point_type},
    ).json()
    return p["id"], pt["id"]


def _add(client, point_id: int, d: str, v: float | None, component: str = "value"):
    return client.post(
        f"/api/v1/points/{point_id}/records",
        json={"series_type": "settlement", "component": component, "obs_date": d, "value_mm": v},
    )


def _void(client, record_id: int):
    return client.delete(f"/api/v1/records/{record_id}")


# ────────────────── 1. 列表接口不得返回作废记录 ──────────────────


def test_void_record_hidden_from_list(client):
    """作废的记录不该出现在记录列表里 —— 这正是"输入框空白"的直接原因。

    前端拿列表去回填输入框，作废那条（值往往是空的）混在里面且排在前面，
    `find()` 就会取到它。
    """
    _, pt = _setup(client)
    r = _add(client, pt, "2026-08-01", 9999.0).json()
    _void(client, r["id"])

    rows = client.get(f"/api/v1/points/{pt}/records").json()
    assert not any(x["id"] == r["id"] for x in rows), "作废记录仍出现在默认列表里"
    assert all(x["status"] != "void" for x in rows), "默认列表不应含 void 状态"


def test_void_record_still_retrievable_explicitly(client):
    """原始数据只增不删：作废的记录仍在库里，显式要求时能查到（可追溯）。"""
    _, pt = _setup(client)
    r = _add(client, pt, "2026-08-01", 9999.0).json()
    _void(client, r["id"])

    rows = client.get(
        f"/api/v1/points/{pt}/records", params={"active_only": False}
    ).json()
    hit = next((x for x in rows if x["id"] == r["id"]), None)
    assert hit is not None, "显式索要全部记录时，作废记录必须还在（原值不可丢）"
    assert hit["value_mm"] == 9999.0, "作废不改数值 —— 原值必须原样保留"


def test_same_day_both_records_listed_only_active(client):
    """替换后的典型状态：同一天同分量有「作废的真实数据」+「生效的演示数据」。

    列表里只应看到生效的那条，且值正确（这正是用户看到的空白框该有的值）。
    """
    _, pt = _setup(client)
    old = _add(client, pt, "2026-08-01", 9999.0).json()
    _void(client, old["id"])
    _add(client, pt, "2026-08-01", 5000.0)  # 同一天同分量再写一条（作废的不占唯一索引）

    rows = client.get(f"/api/v1/points/{pt}/records").json()
    same = [x for x in rows if x["obs_date"] == "2026-08-01"]
    assert len(same) == 1, f"同一天同分量应只返回 1 条生效记录，实际 {len(same)} 条"
    assert same[0]["value_mm"] == 5000.0, "取到的应是生效那条的值"


# ────────────────── 2. 作废记录不得参与计算 ──────────────────


def test_void_record_excluded_from_calculation(client):
    """作废的读数不能成为 V0 —— 否则整条累计序列都错。

    这条最要紧：作废记录若漏进计算，它会当上"首期读数"成为基准，
    后面每一期的累计变化都会偏掉，而且从界面上看不出来。
    """
    _, pt = _setup(client)
    bad = _add(client, pt, "2026-08-01", 9999.0).json()
    _void(client, bad["id"])
    _add(client, pt, "2026-08-02", 5000.0)

    rows = client.get(
        f"/api/v1/points/{pt}/computed",
        params={"series_type": "settlement", "component": "value"},
    ).json()
    assert len(rows) == 1, f"作废那期不该出现在序列里，实际 {len(rows)} 期"
    assert rows[0]["obs_date"] == "2026-08-02"
    assert rows[0]["cumulative_mm"] == 0, "首期生效读数的累计必须为 0（说明它才是 V0）"


def test_void_does_not_shift_later_cumulative(client):
    """作废一期后，后续各期的累计变化不受影响。"""
    _, pt = _setup(client)
    _add(client, pt, "2026-08-01", 5000.0)
    mid = _add(client, pt, "2026-08-02", 5010.0).json()

    before = client.get(
        f"/api/v1/points/{pt}/computed",
        params={"series_type": "settlement", "component": "value"},
    ).json()
    cum_before = {r["obs_date"]: r["cumulative_mm"] for r in before}

    _void(client, mid["id"])
    _add(client, pt, "2026-08-02", 5000.0)  # 同位置补一条新值

    after = client.get(
        f"/api/v1/points/{pt}/computed",
        params={"series_type": "settlement", "component": "value"},
    ).json()
    cum_after = {r["obs_date"]: r["cumulative_mm"] for r in after}
    assert cum_after["2026-08-01"] == cum_before["2026-08-01"] == 0
    assert cum_after["2026-08-02"] == 0, "补的新值等于 V0，累计应为 0（若算进作废的 5010 就不是 0）"


# ────────────────── 3. 作废后必须能补录 ──────────────────


def test_can_reenter_same_day_after_void(client):
    """作废后同一天同分量可以重新录入。

    原缺陷：`get_active_by_key` 只判 `superseded_by_id IS NULL`，
    作废记录仍被当成"已存在的生效记录" → 补录被拒（409），用户改不了数据。
    """
    _, pt = _setup(client)
    r = _add(client, pt, "2026-08-01", 9999.0).json()
    _void(client, r["id"])

    again = _add(client, pt, "2026-08-01", 5000.0)
    assert again.status_code == 201, (
        f"作废后应能补录，实际 HTTP {again.status_code}：{again.text[:200]}"
    )


# ────────────────── 4. 前端兜底（源码守卫） ──────────────────


def test_frontend_filters_inactive_records():
    """前端回填时必须自己再滤一遍作废记录。

    后端已经过滤，但前端这一层是防"旧版本后端 / 缓存数据"的兜底 ——
    这个 bug 的代价是用户完全看不到数据，多一道防线值得。
    """
    src = (SRC / "pages" / "RecordsPage.tsx").read_text(encoding="utf-8")
    assert "r.status !== 'void'" in src, (
        "记录页回填时没有排除作废记录：同一天同分量有两条时会取到作废那条，输入框会空白"
    )
    assert "r.superseded_by_id == null" in src, "回填时也应排除「已被更正替代」的记录"


def test_repo_has_single_inactive_judgement():
    """「哪些状态算无效」只能定义一次，其他模块 import 引用。

    曾经数据生成器自己写了一份 `_REPLACED_STATUS`，repository 又漏判 void ——
    同一个概念两处不一致，直接导致本次事故。
    """
    repo = (
        Path(__file__).resolve().parent.parent
        / "backend" / "repositories" / "observation_repo.py"
    ).read_text(encoding="utf-8")
    gen = (
        Path(__file__).resolve().parent.parent
        / "backend" / "services" / "data_generator_service.py"
    ).read_text(encoding="utf-8")

    assert repo.count('INACTIVE_STATUS = ("void", "superseded")') == 1, (
        "无效状态的元组定义在 repository 里必须且只能有一处"
    )
    assert '("void", "superseded")' not in gen, (
        "data_generator_service 又自己写了一份无效状态定义，应 import INACTIVE_STATUS"
    )
    assert "list_by_point" in repo
