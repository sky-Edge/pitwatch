# -*- coding: utf-8 -*-
"""Report Engine 测试：Excel 按模板版式导出 + Word 日报。"""
import io

import openpyxl

from backend.reports.excel_report import build_period_excel
from backend.reports.docx_report import build_daily_docx


def _setup(client):
    """项目 + 2 测点 + 多期沉降/位移数据 + 预警规则。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "八号基坑测试", "location": "示例公路"}).json()
    pid = p["id"]
    j1 = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "both"}).json()
    j2 = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC2", "point_type": "both"}).json()
    client.put(
        f"/api/v1/projects/{pid}/warning-rules",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 10},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40, "threshold_rate_mm_per_day": 12},
                {"indicator": "displacement", "level": "control", "threshold_cumulative_mm": 20, "threshold_rate_mm_per_day": 3},
                {"indicator": "displacement", "level": "alarm", "threshold_cumulative_mm": 30, "threshold_rate_mm_per_day": 4},
            ],
            "source_note": "测试",
        },
    )
    # 沉降：JJC1 4907→4880(超速) JJC2 5200→5191(累计-9正常)
    for pt, vals in [
        (j1["id"], [("2026-07-30", 4907.0), ("2026-07-31", 4880.0), ("2026-08-01", 4870.0)]),
        (j2["id"], [("2026-07-30", 5200.0), ("2026-07-31", 5191.0)]),
    ]:
        for d, v in vals:
            client.post(
                f"/api/v1/points/{pt}/records",
                json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
            )
    # 位移 X：JJC1 两期
    for d, v in [("2026-07-30", 3413055112.0), ("2026-07-31", 3413055108.0)]:
        client.post(
            f"/api/v1/points/{j1['id']}/records",
            json={"series_type": "displacement", "component": "X", "obs_date": d, "value_mm": v},
        )
    return pid


def _get_session():
    from backend.core.db import SessionLocal

    return SessionLocal()


def test_build_period_excel_template_layout(client):
    """Excel 导出：Sheet 结构复刻模板（竖向位移 + r.立柱结构水平位移），数据单位 m、变化 mm、预警列填写。"""
    pid = _setup(client)
    db = _get_session()
    try:
        buf, filename, info = build_period_excel(db, pid, None)
    finally:
        db.close()
    assert filename.endswith(".xlsx")
    assert info["settlement_rows"] == 2
    assert info["displacement_rows"] == 1  # JJC1 X（JJC1 both 才有位移）

    wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
    assert wb.sheetnames == ["竖向位移", "r.立柱结构水平位移"]

    ws = wb["竖向位移"]
    assert ws["A1"].value == "沉降监测报表"
    assert "八号基坑测试" in str(ws["A2"].value)
    assert "2026-08-01" in str(ws["A3"].value)  # 缺省取最近一期
    # 双层表头
    assert "监测" in str(ws["A5"].value)
    assert ws["I5"].value == "控制值"
    assert ws["I6"].value == "累计变化量（mm)"
    assert ws["J6"].value == "变化速率（mm/d）"
    # 数据行
    assert ws["A7"].value == "JJC1"
    # 读值 m：4907/1000 → 4.907；当前 4870/1000 → 4.87
    assert abs(float(ws["B7"].value) - 4.907) < 1e-9
    assert abs(float(ws["D7"].value) - 4.87) < 1e-9
    # 变化量 mm
    assert abs(float(ws["F7"].value) - (-37.0)) < 1e-9   # 累计 -37
    # 控制值内嵌
    assert float(ws["I7"].value) == 30
    assert float(ws["J7"].value) == 10
    # 预警等级列：**默认不写**（2026-09-30 用户要求「导出的 Excel 不要显示预警」）。
    # 注意这不是"值变正常了"：JJC1 这天累计 -37mm 确实触发了 control(30)，
    # 只是这一列按用户口径留空。口径开关见 backend/reports/export_options.py。
    assert ws["K7"].value in ("", None), f"预警列不该出现在导出文件里：{ws['K7'].value!r}"
    assert ws["A8"].value == "JJC2"
    assert abs(float(ws["D8"].value) - 5.191) < 1e-9  # 5191mm → 5.191 m
    assert ws["K8"].value in ("", None)
    wb.close()


def test_build_period_excel_displacement_sheet(client):
    """位移 Sheet：X/Y 两行 + 数值全填（预警列与整行标红已按用户要求去掉）。"""
    pid = _setup(client)
    db = _get_session()
    try:
        buf, _, info = build_period_excel(db, pid, None)
    finally:
        db.close()
    assert info["displacement_rows"] >= 1
    wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
    ws = wb["r.立柱结构水平位移"]
    assert ws["A1"].value == "基坑水平位移监测日报表"
    # JJC1 X 行存在
    assert ws["A7"].value == "JJC1"
    assert abs(float(ws["D7"].value) - 3413055108.0 / 1000) < 1e-6
    wb.close()


def test_build_daily_docx(client):
    """Word 日报生成。"""
    pid = _setup(client)
    db = _get_session()
    try:
        buf, filename, info = build_daily_docx(db, pid, None)
    finally:
        db.close()
    assert filename.endswith(".docx")
    assert info["rows"] > 0
    assert buf.getvalue().startswith(b"PK")  # docx 是 zip
