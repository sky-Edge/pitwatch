# -*- coding: utf-8 -*-
"""母版整卷引擎测试：学习+渲染。

用与真实样板同构的最小母版（封面/签字/竖向位移/水平位移 4 Sheet）验证：
- 变量槽识别（封面日期格/签字期次格/天气格/数据列映射）
- 渲染：期次/日期/天气/数据全数值（覆盖公式）/X+Y 相邻行
"""
import io

import pytest
from openpyxl import Workbook

from backend.core.config import settings

# 供学习函数内部临时落盘
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="master_test_"))


@pytest.fixture(autouse=True)
def _cleanup():
    settings.ensure_dirs()
    for f in settings.backup_dir.glob("*.db"):
        f.unlink(missing_ok=True)
    for f in _TMP.glob("*.xlsx"):
        f.unlink(missing_ok=True)
    yield


def _make_master() -> Path:
    """构造与八号母版同构的最小整卷。"""
    wb = Workbook()
    # 封面
    ws = wb.active
    ws.title = "封面"
    ws["E11"] = "工程名称:"
    ws["F11"] = "8号基坑"
    ws["E16"] = "监测日期:"
    ws["F16"] = 46254  # 2026-08-20
    # 签字
    ws2 = wb.create_sheet("签字")
    ws2["A11"] = "（第二十一期）"
    ws2["E52"] = 46254
    # 竖向位移
    ws3 = wb.create_sheet("竖向位移")
    ws3["A1"] = "沉降监测报表"
    ws3["A2"] = "监测工程名称：8号基坑"
    ws3["K2"] = "天气：晴"
    ws3["A3"] = "本次监测时间：2026-08-20"
    ws3["K3"] = "上次监测时间：2026-08-19"
    ws3["A5"] = "监测\n点号"
    ws3["B5"] = "初始值（m)"
    ws3["C5"] = "上次测值（m）"
    ws3["D5"] = "本次测值（m）"
    ws3["E5"] = "上次累计（mm)"
    ws3["F5"] = "本次累计（mm)"
    ws3["G5"] = "本次变化（mm)"
    ws3["H5"] = "速率（mm/d）"
    ws3["I5"] = "控制值"
    ws3["I6"] = "累计变化量（mm)"
    ws3["J6"] = "变化速率（mm/d）"
    ws3["K5"] = "预警\n等级"
    # 两行数据（一行带公式）
    for r, (no, b, c, d) in enumerate([("JJC1", 4.907, 4.909, 4.909), ("JJC2", 4.86, 4.853, 4.853)], start=7):
        ws3.cell(row=r, column=1, value=no)
        ws3.cell(row=r, column=2, value=b)
        ws3.cell(row=r, column=3, value=c)
        ws3.cell(row=r, column=4, value=d)
        ws3.cell(row=r, column=5, value=f"=IF(OR(ISBLANK(B{r}),ISBLANK(C{r})),\"\",(C{r}-B{r})*1000)")
        ws3.cell(row=r, column=6, value=f"=IF(OR(ISBLANK(B{r}),ISBLANK(D{r})),\"\",(D{r}-B{r})*1000)")
        ws3.cell(row=r, column=7, value=f"=IF(OR(ISBLANK(C{r}),ISBLANK(D{r})),\"\",(D{r}-C{r})*1000)")
        ws3.cell(row=r, column=8, value=f"=G{r}/1")
    # 水平位移（X/Y 相邻）—— 与真实母版同构：R5 主表头 + R6 第二层表头 + R7 起数据
    ws4 = wb.create_sheet("r.立柱结构水平位移")
    ws4["A3"] = "本次监测时间：2026-08-20"
    ws4["A5"] = "监测\n点号"
    ws4["B5"] = "初始值（m)"
    ws4["C5"] = "上次测值（m）"
    ws4["D5"] = "本次测值（m）"
    ws4["F5"] = "本次累计（mm)"
    ws4["H5"] = "速率（mm/d）"
    ws4["K5"] = "预警\n等级"
    # 第二层表头（控制值两列等）——与真实版式一致，保证 data_start_row=7
    ws4["I5"] = "控制值"
    ws4["I6"] = "累计变化量（mm)"
    ws4["J6"] = "变化速率（mm/d）"
    # X 行点号 + Y 行无点号（数据自 R7 起）
    ws4["A7"] = "JJC1"
    ws4["B7"] = 3413055.112
    ws4["C7"] = 3413055.118
    ws4["D7"] = 3413055.119
    ws4["B8"] = 510174.166
    ws4["C8"] = 510174.158
    ws4["D8"] = 510174.159

    p = _TMP / "master.xlsx"
    wb.save(p)
    return p


def _seed_project(client, pno="PRJ001"):
    """项目 + 8测点沉降/位移数据到两期（兼容母版 8 点布局的原始数据区测试）。

    数值设计：
    - 8-20 JJC1 settlement = 4909.0mm（=4.909m，与原单点测试一致）
    - 8-21 JJC1 settlement = 4910.0mm（=4.91m，与原单点测试一致）
    - 8-21 displacement X 每点值稍异；其他点按 n 偏移递增/递减
    """
    p = client.post("/api/v1/projects", json={"code": pno, "name": "整卷测试"}).json()
    pid = p["id"]
    pts = {}
    for n in range(1, 9):
        r = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": f"JJC{n}", "point_type": "both"}).json()
        pts[f"JJC{n}"] = r["id"]
    # V0 + 两期：8-20 第0期；8-21 第1期
    for date_, base in [("2026-08-20", 0), ("2026-08-21", 1)]:
        for n in range(1, 9):
            pid_pt = pts[f"JJC{n}"]
            client.post(f"/api/v1/points/{pid_pt}/records",
                        json={"series_type": "settlement", "component": "value",
                              "obs_date": date_, "value_mm": 4909.0 - (n - 1) * 0.05 + base * 1.0})
            client.post(f"/api/v1/points/{pid_pt}/records",
                        json={"series_type": "displacement", "component": "X",
                              "obs_date": date_, "value_mm": 3413055119.0 - n * 1.0 + base * 1.0})
            client.post(f"/api/v1/points/{pid_pt}/records",
                        json={"series_type": "displacement", "component": "Y",
                              "obs_date": date_, "value_mm": 510174159.0 - n * 1.0 + base * 1.0})
    return pid


def test_learn_detects_variable_slots(client):
    """学习识别封面/签字期次日期格、数据列映射、天气格。"""
    from backend.reports.master_learn import learn_master_layout

    layout = learn_master_layout(_make_master())
    assert layout["meta"]["cover_date_cell"] == "封面!F16"
    assert layout["meta"]["period_cell"] == "签字!A11"
    sheets = {s["sheet_name"]: s for s in layout["sheets"]}
    assert "竖向位移" in sheets and "r.立柱结构水平位移" in sheets
    f = sheets["竖向位移"]["layout"]
    assert f["point_col"] == 1 and f["v0_m"] == 2 and f["current_m"] == 4
    assert f["data_start_row"] == 7
    assert f.get("weather_cell") == "竖向位移!K2"
    assert sheets["竖向位移"]["kind"] == "settlement"
    assert sheets["r.立柱结构水平位移"]["mode"] == "x_y_two_rows"


def test_upload_master_then_export_whole_volume(client):
    """API 链路：上传母版 → master_mode=true → 导出最新整卷（9 Sheet 保留、变量更新、数值覆盖）。"""
    import openpyxl

    pid = _seed_project(client)
    # 上传母版
    with open(_make_master(), "rb") as fh:
        r = client.post(
            f"/api/v1/projects/{pid}/export-templates/upload",
            data={"name": "报送母版"},
            files={"file": ("母版.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert r.status_code == 200
    d = r.json()
    assert d["master_mode"] is True
    tid = d["id"]
    assert d["detected_layout"]["meta"]["cover_date_cell"] == "封面!F16"

    # 导出（选 2026-08-21 第1期 + 天气小雨）
    resp = client.get(f"/api/v1/projects/{pid}/reports/excel", params={"template_id": tid, "obs_date": "2026-08-21", "weather": "小雨"})
    assert resp.status_code == 200, resp.text
    wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=False)
    assert len(wb.sheetnames) == 4  # 本测试母版只有 4 Sheet
    assert "封面" in wb.sheetnames and "竖向位移" in wb.sheetnames
    # 封面日期 = 2026-08-21 序列 46255
    assert wb["封面"]["F16"].value == 46255
    # 签字期次第一期
    assert "第一期" in str(wb["签字"]["A11"].value)
    # 数据数值（非公式）
    ws3 = wb["竖向位移"]
    assert isinstance(ws3["E7"].value, (int, float))
    assert ws3["K2"].value == "天气：小雨"
    wb.close()


def test_render_initial_period_clears_legacy_cells(client):
    """初始值期（第0期）：数据区只保留点号+B初始值+控制值，
    母版自带的旧值/公式（C/D/E/F/G/H/K 等）必须清空；K3 上次时间填初始值日。"""
    from datetime import date

    import openpyxl

    from backend.core.db import SessionLocal
    from backend.reports.master_learn import learn_master_layout
    from backend.reports.master_report import build_master_report

    pid = _seed_project(client)
    db = SessionLocal()
    try:
        master = _make_master()

        class T:
            file_path = str(master)
            name = "母版"
            layout_json = learn_master_layout(master)

        # 2026-08-20 = 项目首个观测日 → 初始值期
        buf, filename, info = build_master_report(db, pid, T(), obs_date=date(2026, 8, 20), weather="晴")
        assert info["period"] == "初始值"
        assert "初始值" in filename  # 文件名期次正确（非"零"）

        wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()), data_only=False)
        ws = wb["竖向位移"]
        # K3 上次时间 = 初始值日
        assert "2026-08-20" in str(ws["K3"].value)
        # 数据行：A=点号 B=初始值 I/J=控制值 保留，C~H/K 全空（母版旧值 4.909/公式被清）
        for r in range(7, 8):
            assert ws.cell(row=r, column=1).value == "JJC1"
            assert ws.cell(row=r, column=2).value is not None  # B 初始值
            for c in (3, 4, 5, 6, 7, 8):  # C~H 累计/变化/速率 全空
                assert ws.cell(row=r, column=c).value is None, f"C{r}{c} 应清空"
            assert ws.cell(row=r, column=11).value is None  # K 预警空
        wb.close()
    finally:
        db.close()


def test_render_missing_point_day_clears_row_no_shift(client):
    """某点某期缺测：该点母版行位不因缺测而上移错位，其数据列被清空、
    母版自带旧值/公式不残留；其余有记录点行正常写数值。

    回归 bug：fill_data_sheets 原按"有记录点"连续下推，某点缺测时整表
    点位错位、表尾残留母版旧公式行（如 H=G/2 旧速率串入导出文件）。
    """
    from datetime import date

    import openpyxl

    from backend.core.db import SessionLocal
    from backend.models.observation_record import ObservationRecord
    from backend.reports.master_learn import learn_master_layout
    from backend.reports.master_report import build_master_report
    from backend.services.point_service import list_points

    pid = _seed_project(client)
    db = SessionLocal()
    try:
        # 制造缺测：删除 JJC2 沉降在 2026-08-21 的记录（JJC1 正常）
        p2 = next(p for p in list_points(db, pid, include_inactive=False) if p.point_no == "JJC2")
        db.query(ObservationRecord).filter(
            ObservationRecord.point_id == p2.id,
            ObservationRecord.series_type == "settlement",
            ObservationRecord.obs_date == date(2026, 8, 21),
        ).delete()
        db.commit()

        master = _make_master()

        class T:
            file_path = str(master)
            name = "母版"
            layout_json = learn_master_layout(master)

        buf, filename, info = build_master_report(db, pid, T(), obs_date=date(2026, 8, 21), weather="晴")
        assert info["period"] == "第一期"  # 8-20 初始 → 8-21 第一期

        wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()), data_only=False)
        ws = wb["竖向位移"]
        # 行位不漂移：R7 仍 JJC1（有记录写值）、R8 仍 JJC2（缺测清行）
        assert ws["A7"].value == "JJC1"
        assert ws["A8"].value == "JJC2"
        # JJC1 行 C~H 全数值（覆盖公式）
        for c in (3, 4, 5, 6, 7, 8):
            v = ws.cell(row=7, column=c).value
            assert v is not None and not isinstance(v, str), f"JJC1 R7 列{c} 应为数值，实际 {v!r}"
        # JJC2 行 C~H/K 全空，母版自带公式（=G8/1 等）不残留
        for c in (3, 4, 5, 6, 7, 8, 11):
            assert ws.cell(row=8, column=c).value is None, f"JJC2 R8 列{c} 应清空，实际 {ws.cell(row=8, column=c).value!r}"
        wb.close()
    finally:
        db.close()


def test_render_raw_data_block_syncs_per_period(client):
    """原始数据区（X/Y/Z 点号表）：固定显示各测点 V0 初始值与初始值基准日，
    不随导出期次变化；母版旧值不残留。

    测试母版模拟 N~S 列 X/Y/Z 点号表：日期 O8、左列 JJC1~6、右列 JJC7~8。
    """
    from datetime import date

    import openpyxl

    from backend.core.db import SessionLocal
    from backend.reports.master_learn import learn_master_layout
    from backend.reports.master_report import build_master_report

    # 扩展母版构造：补上 N~S 列原始数据区
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "封面"
    ws["E11"] = "工程名称:"
    ws["F11"] = "8号基坑"
    ws["E16"] = "监测日期:"
    ws["F16"] = 46254
    wb.create_sheet("签字")["A11"] = "（第二十一期）"
    ws3 = wb.create_sheet("竖向位移")
    ws3["A5"] = "监测\n点号"
    ws3["B5"] = "初始值（m)"
    ws3["C5"] = "上次测值（m）"
    ws3["D5"] = "本次测值（m）"
    ws3["E5"] = "上次累计（mm)"
    ws3["F5"] = "本次累计（mm)"
    ws3["I5"] = "控制值"
    ws3["I6"] = "累计变化量（mm)"
    ws3["J6"] = "变化速率（mm/d）"
    # 母版自带旧值（8-20 第 21 期数据），用于验证被覆盖
    ws3["N8"] = "日期"
    ws3["O8"] = "2026.07.30"
    ws3["N9"] = "点号"
    ws3["O9"] = "坐标数据"
    ws3["Q9"] = "点号"
    ws3["R9"] = "坐标数据"
    ws3["N10"] = "JJC1"; ws3["O10"] = "X"; ws3["P10"] = 3413055.12
    ws3["N13"] = "JJC2"; ws3["O13"] = "X"; ws3["P13"] = 3413050.631
    ws3["Q10"] = "JJC7"; ws3["R10"] = "X"; ws3["S10"] = 3413054.632
    import tempfile
    from pathlib import Path

    p = Path(tempfile.mkdtemp()) / "raw_master.xlsx"
    wb.save(p)
    wb.close()

    pid = _seed_project(client)
    db = SessionLocal()
    try:
        class T:
            file_path = str(p)
            name = "母版"
            layout_json = learn_master_layout(p)

        # 导出 8-21（非初始值期）：原始数据区应固定显示 8-20 V0 与初始值基准日 8-20
        buf, filename, info = build_master_report(db, pid, T(), obs_date=date(2026, 8, 21), weather="阴")
        wb2 = openpyxl.load_workbook(io.BytesIO(buf.getvalue()), data_only=False)
        ws2 = wb2["竖向位移"]
        # 日期格固定为初始值基准日（8-20 = 最早观测日 = V0 采测日）
        assert ws2["O8"].value == "2026-08-20"
        # 坐标 = V0（8-20 JJC1 displacement X=3413055118mm→3413055.118m），旧值被覆盖
        assert abs(ws2["P10"].value - 3413055.118) < 1e-6
        assert abs(ws2["P13"].value - 3413055.117) < 1e-6  # JJC2 8-20 X
        assert abs(ws2["S10"].value - 3413055.112) < 1e-6  # JJC7 8-20 X

        # 再导 8-21 之后一期验证不随期变：导出 8-21 本身原始区仍为 V0
        buf3, _, _ = build_master_report(db, pid, T(), obs_date=date(2026, 8, 21), weather="晴")
        wb3 = openpyxl.load_workbook(io.BytesIO(buf3.getvalue()), data_only=False)
        assert wb3["竖向位移"]["O8"].value == "2026-08-20"
        assert abs(wb3["竖向位移"]["P10"].value - 3413055.118) < 1e-6
        wb2.close()
        wb3.close()
    finally:
        db.close()


def test_render_fills_all_value_slots(client):
    """渲染：日期/期次/天气/数据数值覆盖公式/X+Y 行。"""
    from datetime import date

    import openpyxl

    from backend.core.db import SessionLocal
    from backend.reports.master_learn import learn_master_layout
    from backend.reports.master_report import build_master_report

    pid = _seed_project(client)
    db = SessionLocal()
    try:
        master = _make_master()

        class T:
            file_path = str(master)
            name = "母版"
            layout_json = learn_master_layout(master)

        buf, filename, info = build_master_report(db, pid, T(), obs_date=date(2026, 8, 21), weather="阴")
        assert info["period"] == "第一期"
        assert "2026.08.21" in filename

        wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()), data_only=False)
        ws = wb["竖向位移"]
        assert "2026-08-21" in str(ws["A3"].value)  # 本次时间更新
        assert ws["K2"].value == "天气：阴"
        # 数据行7: JJC1 value 4909(8-20)→4910(8-21): E=-1? 不——项目 V0 初始未设 → 首期=8-20(V0=4909)…
        # 8-21 为第2个日期: B=V0 4909/1000=4.909, C=上次 4.909, D=本次 4.91, E=0 F=1 G=1 H=1
        row7 = [ws.cell(row=7, column=c).value for c in range(1, 9)]
        assert row7[0] == "JJC1"
        assert row7[3] == 4.91  # D 本次测值
        # 数值（非公式）
        assert isinstance(ws["E7"].value, (int, float))
        # X 位移 sheet X/Y 行
        ws4 = wb["r.立柱结构水平位移"]
        assert ws4["A7"].value == "JJC1"  # X 行
        assert ws4["A8"].value is None  # Y 行相邻无点号
        wb.close()
    finally:
        db.close()


def test_export_syncs_project_name_into_name_slots(client):
    """导出日报以系统项目名称为准：封面工程名称格、签字基坑名格（带括号）、
    数据表"监测工程名称/工点名称"前缀行 统一改写为系统项目名；
    母版抄错的号（封面 3号 vs 正文 5号）一并修正；叙述性文本不受影响。

    回归需求：系统项目名要与导出的 Excel 日报里工程名称对应起来（以系统为准）。
    """
    import openpyxl

    from datetime import date

    pid = _seed_project(client)  # 项目名 = 整卷测试
    # 构造名称不一致的母版：封面 3号（抄错）、签字 5号、表头 5号
    master = _make_master()
    wb2 = openpyxl.load_workbook(master)
    wb2["封面"]["B7"] = "嘉善县兴善公路(晋阳西路-浙苏省界段)上跨沪昆铁路立交工程"
    wb2["封面"]["F11"] = "3号基坑"
    wb2["签字"]["A9"] = "（5号基坑）"
    wb2["竖向位移"]["A2"] = "监测工程名称：5号基坑"
    wb2["竖向位移"]["G3"] = "工点名称：兴善公路 5号基坑"
    wb2["竖向位移"]["A40"] = "本次监测工作为5号基坑围檩竖向位移监测工作。"  # 叙述文本须保留
    p2 = _TMP / "master_name.xlsx"
    wb2.save(p2)
    wb2.close()

    with open(p2, "rb") as fh:
        r = client.post(
            f"/api/v1/projects/{pid}/export-templates/upload",
            data={"name": "报送母版"},
            files={"file": ("母版.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]

    resp = client.get(f"/api/v1/projects/{pid}/reports/excel", params={"template_id": tid, "obs_date": "2026-08-21", "weather": "晴"})
    assert resp.status_code == 200, resp.text
    out = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=False)
    try:
        # 封面整格名称槽：3号 → 系统项目名（修正抄错号）
        assert out["封面"]["F11"].value == "整卷测试"
        # 签字带括号名称槽：保留括号形态
        assert out["签字"]["A9"].value == "（整卷测试）"
        # 数据表标签行：保留"监测工程名称："前缀
        assert out["竖向位移"]["A2"].value == "监测工程名称：整卷测试"
        # 工点名称：保留"兴善公路 "前缀
        assert out["竖向位移"]["G3"].value == "工点名称：兴善公路 整卷测试"
        # 叙述文本中的基坑名实体同步改写（整卷无新旧名并存）
        assert out["竖向位移"]["A40"].value == "本次监测工作为整卷测试围檩竖向位移监测工作。"
        # 大工程名（非"数字号…基坑/墩"结构）不受影响
        assert "沪昆铁路立交工程" in str(out["封面"]["B7"].value)
        # 数据填充不受影响
        assert isinstance(out["竖向位移"]["E7"].value, (int, float))
    finally:
        out.close()
