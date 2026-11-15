# -*- coding: utf-8 -*-
"""合并报表：南北两侧合出**一份**整卷。

守的几条：
- 一张表只装一个项目的数（北侧 9 行、南侧 7 行，不能串）；
- 一张册子只有一份封面/签字，期次按主项目；
- 每张表自己的"本次监测时间"各写各的项目；
- 指定日期谁没数据就**报错**，绝不拿旧一期凑页；
- 文字页要把两侧都说到（点号带项目名，否则 QC01 撞名字）。
"""
import shutil
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from backend.core.config import settings
from backend.core.db import SessionLocal
from backend.models.export_template import ExportTemplate
from backend.reports.master_learn import learn_master_layout
from backend.reports.merged_report import build_merged_report
from backend.services import report_group_service
from tests.conftest import put_control_rules

MASTER = Path(__file__).parent / "fixtures" / "钢栈桥合并母版.xlsx"

NORTH = [("QC01", 2975.6), ("QC02", 2985.8), ("QC03", 2984.6), ("QC04", 2972.1),
         ("QC05", 2980.8), ("QC06", 2982.4), ("QC07", 2982.6), ("QC08", 2971.4),
         ("QC09", 2960.3)]
SOUTH = [("QC01", 2042.0), ("QC02", 2060.0), ("QC03", 2060.0), ("QC04", 2078.0),
         ("QC05", 2051.0), ("QC06", 2059.0), ("QC07", 2080.0)]


def _mk_project(client, name: str, points: list[tuple[str, float]]) -> int:
    r = client.post("/api/v1/projects", json={"code": name, "name": name})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    for no, v0 in points:
        r = client.post(
            f"/api/v1/projects/{pid}/points",
            json={"point_no": no, "point_type": "settlement", "initial_value_mm": v0},
        )
        assert r.status_code == 201, r.text
    put_control_rules(client, pid)
    return pid


def _add_records(client, pid: int, obs_date: str, drop_mm: float) -> None:
    """给所有测点写同一天读数：初始值 - drop_mm。"""
    pts = client.get(f"/api/v1/projects/{pid}/points").json()
    for p in pts:
        r = client.post(
            f"/api/v1/points/{p['id']}/records",
            json={
                "series_type": "settlement",
                "component": "value",
                "obs_date": obs_date,
                "value_mm": (p["initial_value_mm"] or 0) - drop_mm,
            },
        )
        assert r.status_code == 201, r.text


def _mk_template() -> int:
    dest = settings.templates_dir / "_library" / "test_merged" / "master.xlsx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(MASTER, dest)
    layout = learn_master_layout(dest)
    assert layout.get("sheets") and layout.get("meta"), "夹具母版自身识别失败"

    db = SessionLocal()
    try:
        tpl = ExportTemplate(
            name="钢栈桥合并母版",
            template_type="excel",
            is_builtin=False,
            is_global=True,
            file_path=str(dest),
            layout_json=layout,
        )
        db.add(tpl)
        db.commit()
        return tpl.id
    finally:
        db.close()


def _mk_group(client, tpl_id: int, north: int, south: int) -> int:
    r = client.post(
        "/api/v1/report-groups",
        json={
            "name": "钢栈桥监测日报",
            "display_name": "钢栈桥",
            "template_id": tpl_id,
            "main_project_id": north,
            "items": [
                {"project_id": north, "sheet_name": "北侧沉降表", "sort_order": 0},
                {"project_id": south, "sheet_name": "南侧沉降表", "sort_order": 1},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _build(gid: int, obs_date=None):
    db = SessionLocal()
    try:
        group = report_group_service.get_group(db, gid)
        return build_merged_report(db, group, obs_date)
    finally:
        db.close()


def _setup(client, drop_south: float = 0.3):
    tpl_id = _mk_template()
    north = _mk_project(client, "北侧钢栈桥", NORTH)
    south = _mk_project(client, "南侧钢栈桥", SOUTH)
    _add_records(client, north, "2026-07-27", 0.1)   # 初始值期
    _add_records(client, south, "2026-07-27", 0.1)
    _add_records(client, north, "2026-07-28", 0.3)   # 第 1 期
    _add_records(client, south, "2026-07-28", drop_south)
    return tpl_id, north, south


def test_merged_report_fills_each_sheet_with_its_own_project(client):
    tpl_id, north, south = _setup(client)
    gid = _mk_group(client, tpl_id, north, south)

    buf, filename, info = _build(gid, date(2026, 7, 28))
    wb = load_workbook(BytesIO(buf.getvalue()), data_only=True)

    # 一张册子 = 两张数据表 + 共用页
    assert "北侧沉降表" in wb.sheetnames and "南侧沉降表" in wb.sheetnames
    assert "封面" in wb.sheetnames and "签字" in wb.sheetnames

    n = wb["北侧沉降表"]
    s = wb["南侧沉降表"]

    # 北侧 9 行、南侧 7 行，各自装自己的项目
    assert n["A7"].value == "QC01" and n["A15"].value == "QC09"
    assert s["A7"].value == "QC01" and s["A13"].value == "QC07"
    assert s["A14"].value is None, "南侧只有 7 个点，不能留第 8、9 行"

    # 初始值 / 本次测值 / 累计 三列对得上（北侧 QC01：2975.6 → 2975.3，累计 -0.3）
    assert n["B7"].value == pytest.approx(2.9756, abs=1e-4)
    assert n["D7"].value == pytest.approx(2.9753, abs=1e-4)
    assert n["F7"].value == pytest.approx(-0.3, abs=1e-3)
    # 南侧 QC01：2042.0 → 2041.7
    assert s["B7"].value == pytest.approx(2.0420, abs=1e-4)
    assert s["D7"].value == pytest.approx(2.0417, abs=1e-4)

    # 每张表自己的监测时间
    assert "2026-07-28" in str(n["A3"].value)
    assert "2026-07-28" in str(s["A3"].value)
    # 两侧工点名称不能被合并成同一个
    def _row_text(ws, r: int) -> str:
        return " ".join(str(ws.cell(row=r, column=c).value or "") for c in range(1, 20))

    assert "北侧" in _row_text(n, 3), _row_text(n, 3)
    assert "南侧" in _row_text(s, 3), _row_text(s, 3)

    # 期次按主项目：7.27 是初始值期，7.28 = 第一期（系统统一用中文数字）
    assert "第一期" in str(wb["签字"]["A6"].value)
    assert info["sheets"] == [
        {"sheet_name": "北侧沉降表", "project": "北侧钢栈桥", "obs_date": "2026-07-28"},
        {"sheet_name": "南侧沉降表", "project": "南侧钢栈桥", "obs_date": "2026-07-28"},
    ]


def test_merged_text_pages_mention_both_sides(client):
    # 南侧降得更多 → 极值一南一北，文字里必须两个项目名都出现
    tpl_id, north, south = _setup(client, drop_south=0.8)
    gid = _mk_group(client, tpl_id, north, south)
    buf, _name, _info = _build(gid, date(2026, 7, 28))
    wb = load_workbook(BytesIO(buf.getvalue()), data_only=True)

    analysis = " ".join(str(c.value or "") for row in wb["分析"].iter_rows() for c in row)
    assert "北侧钢栈桥 QC" in analysis and "南侧钢栈桥 QC" in analysis, analysis

    conclusion = " ".join(str(c.value or "") for row in wb["成果提交"].iter_rows() for c in row)
    assert "钢栈桥" in conclusion


def test_merged_report_refuses_when_one_side_has_no_data(client):
    tpl_id = _mk_template()
    north = _mk_project(client, "北侧钢栈桥", NORTH)
    south = _mk_project(client, "南侧钢栈桥", SOUTH)
    _add_records(client, north, "2026-07-27", 0.1)
    _add_records(client, north, "2026-07-28", 0.3)
    _add_records(client, south, "2026-07-27", 0.1)   # 南侧没有 7.28
    gid = _mk_group(client, tpl_id, north, south)

    with pytest.raises(ValueError) as ei:
        _build(gid, date(2026, 7, 28))
    msg = str(ei.value)
    assert "2026-07-28" in msg and "南侧钢栈桥" in msg and "最近一期" in msg


def test_merged_report_defaults_to_latest_common_date(client):
    tpl_id = _mk_template()
    north = _mk_project(client, "北侧钢栈桥", NORTH)
    south = _mk_project(client, "南侧钢栈桥", SOUTH)
    _add_records(client, north, "2026-07-27", 0.1)
    _add_records(client, north, "2026-07-28", 0.3)
    _add_records(client, south, "2026-07-27", 0.1)   # 南侧只到 7.27
    gid = _mk_group(client, tpl_id, north, south)

    _buf, _name, info = _build(gid)          # 不给日期 → 取两边都有的最近一天
    assert info["obs_date"] == "2026-07-27"


def test_merged_export_api_returns_one_workbook(client):
    tpl_id, north, south = _setup(client)
    gid = _mk_group(client, tpl_id, north, south)

    r = client.get(f"/api/v1/reports/merged/excel?group_id={gid}&obs_date=2026-07-28")
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]


def test_group_requires_every_sheet_bound(client):
    tpl_id = _mk_template()
    north = _mk_project(client, "北侧钢栈桥", NORTH)
    r = client.post(
        "/api/v1/report-groups",
        json={
            "name": "只绑一半",
            "template_id": tpl_id,
            "main_project_id": north,
            "items": [{"project_id": north, "sheet_name": "北侧沉降表"}],
        },
    )
    assert r.status_code == 400
    assert "南侧沉降表" in r.json()["detail"]
