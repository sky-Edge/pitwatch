# -*- coding: utf-8 -*-
"""界面数值显示：不许把浮点尾巴印到界面上。

用户 2026-11-16 的截图里，首页报警横幅写着：
    「JJC1 累计 -7.200000286102295mm」
这类数字不是计算错，是二进制浮点表示误差原样透到了界面。工程资料里
出现这种数，看的人第一反应就是「这系统算错了」—— 比数字不准还伤。

收口放在 `calc_service.compute_project_warning_status`：首页横幅、详情表、
项目行都从那个 dict 取数，round 一次全部干净；前端 `utils/format.ts` 再兜一道
（记录页手填值、导入旧数据可能绕过后端这条路）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def project_with_long_float(client):
    """造一个累计变化带浮点尾巴的项目（92.7999999 − 100 = -7.2000001）。"""
    p = client.post(
        "/api/v1/projects",
        json={"code": "浮点测试", "name": "浮点测试", "sign_config": {"settlement": {"positive_means": "down"}}},
    ).json()
    pid = p["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "T01", "point_type": "settlement", "initial_value_mm": 100.0},
    ).json()
    # 控制值给 5mm：沉 7.2mm 会进入汇总（只有超限的点才会出现在汇总里，
    # 用 30mm 的话拿不到任何行，测试前提就不成立了）
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {
                    "indicator": "settlement",
                    "level": "control",
                    "threshold_cumulative_mm": 5.0,
                    "threshold_rate_mm_per_day": 10.0,
                    "is_active": True,
                }
            ]
        },
    )
    r = client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={
            "series_type": "settlement",
            "component": "value",
            "obs_date": "2026-05-01",
            "value_mm": 92.7999999,
        },
    )
    assert r.status_code == 201, r.text
    return pid, pt["id"]


def test_summary_cumulative_has_no_float_tail(client, project_with_long_float):
    """汇总接口返回的 cumulative_mm 必须是 2 位以内。"""
    pid, _pt = project_with_long_float
    rows = client.get("/api/v1/warning-summary").json()
    me = [r for r in rows if r["project_id"] == pid][0]

    vals = [p["cumulative_mm"] for p in me["alarm_points"] + me["control_points"]]
    assert vals, "没拿到任何预警点，测试前提不成立"
    for v in vals:
        # round(x, 2) 之后，乘 100 应当是个整数（允许浮点表示带来的极小误差）
        assert abs(v * 100 - round(v * 100)) < 1e-6, f"接口返回了带浮点尾巴的数：{v!r}"


def test_detail_rows_are_rounded_too(client, project_with_long_float):
    """详情弹窗里的读数/变化/累计同样不许带尾巴（它直接复用汇总里的同一个 dict）。"""
    pid, _pt = project_with_long_float
    rows = client.get("/api/v1/warning-summary").json()
    me = [r for r in rows if r["project_id"] == pid][0]

    checked = 0
    for row in me["alarm_points"] + me["control_points"]:
        for key in ("value_mm", "delta_mm", "cumulative_mm", "rate_mm_per_day"):
            v = row.get(key)
            if v is None:
                continue
            checked += 1
            assert abs(v * 100 - round(v * 100)) < 1e-6, f"{key} 带浮点尾巴：{v!r}"
    assert checked > 0, "一个数值都没检到，测试前提不成立"


def test_frontend_uses_the_shared_formatter():
    """前端必须用公共 fmtMm 显示 mm，不许直接插值。

    这条是源码守卫：`${p.cumulative_mm}mm` 这种写法一旦回潮，界面又会冒出长尾小数。
    """
    page = (ROOT / "frontend" / "src" / "pages" / "ProjectsPage.tsx").read_text(encoding="utf-8")
    assert "from '../utils/format'" in page, "ProjectsPage 没引入公共格式化工具"

    # 报警相关的三处文案必须经过 fmtMm
    for needle in ("fmtMm(first.cumulative_mm)", "fmtMm(worst)"):
        assert needle in page, f"ProjectsPage 少了 {needle} —— 裸插值会把浮点尾巴带到界面"

    fmt = ROOT / "frontend" / "src" / "utils" / "format.ts"
    assert fmt.is_file(), "少了公共格式化工具 utils/format.ts"
    body = fmt.read_text(encoding="utf-8")
    assert "toFixed(2)" in body, "fmtMm 必须收到两位小数"
