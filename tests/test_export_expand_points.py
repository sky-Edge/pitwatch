# -*- coding: utf-8 -*-
"""母版导出：项目测点多于母版点位时必须自动补行（2026-09-30 用户报障）。

用户场景：母版是拿**别的基坑**的报表当底模的（8 个点位行），而项目有 12 个测点。
原实现按母版固有行位逐点驱动，多出来的 4 个点**整条消失且没有任何提示**。

用户确认的做法：自动补行 + 下移说明行；反过来（母版点位比项目多）保留空行。

测试里的母版是**现造的最小母版**（结构照抄真实母版：8 个点位 + 12 行空白 + 说明/结论/签字），
不依赖生产目录里用户上传的文件。
"""
from __future__ import annotations

import json
from io import BytesIO
from urllib.parse import unquote
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, Side
from openpyxl.utils import get_column_letter

from backend.core.config import settings
from backend.core.db import SessionLocal
from backend.models.export_template import ExportTemplate

SETTLE = "竖向位移"
DISP = "r.立柱结构水平位移"
TAIL_TEXT = "变化量正负号说明："
CONCLUSION_TEXT = "监测结论及建议："
SIGN_TEXT = "现场监测人："
TEMPLATE_POINTS = 8


_THIN = Side(style="thin")


def _bordered(ws, row: int, cols: int = 11) -> None:
    """给点位行加边框（空白预留行**不加** —— 这样补出来的行有没有边框才验得出来）。"""
    for c in range(1, cols + 1):
        ws.cell(row=row, column=c).border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _layout(with_raw: bool = False) -> dict:
    cols = {"point_col": 1, "v0_m": 2, "prev_m": 3, "current_m": 4, "prev_cum_mm": 5,
            "cum_mm": 6, "delta_mm": 7, "rate": 8, "warning_level": 9,
            "control_cum": 10, "control_rate": 11, "data_start_row": 7}
    sheets = [
        {"sheet_name": SETTLE, "kind": "settlement", "layout": dict(cols), "mode": "per_row"},
        {"sheet_name": DISP, "kind": "displacement", "layout": dict(cols), "mode": "x_y_two_rows"},
    ]
    if with_raw:
        sheets[0]["raw_data"] = _raw_config()
    return {"meta": {"sign_date_cell": "签字!E5"}, "sheets": sheets}


# 原始数据区（X/Y/Z 坐标小表）：N~S 列，两列 × 每点 3 行
RAW_TOP = 10          # 第一个点位块的首行
RAW_BOTTOM = 27       # 网格底部（最后一行的 Z）
RAW_LEFT_POINTS = 6   # 母版左列排满 6 个
RAW_RIGHT_POINTS = 2  # 右列只填了 2 个（JJC7/JJC8），下面留着带边框的空格


def _raw_entries(count: int, first_no: int, pc: int, cc: int, vc: int) -> list[dict]:
    """原始数据区一列里各点位块的配置（点号列 / 分量列 / 坐标值列）。"""
    out = []
    for i in range(count):
        r = RAW_TOP + i * 3
        ref = lambda col: f"{SETTLE}!{get_column_letter(col)}{r}"  # noqa: E731
        out.append({"start_row": r, "point_no": f"JJC{first_no + i}",
                    "point_cell": ref(pc), "component_cell": ref(cc), "value_cell": ref(vc)})
    return out


def _raw_config() -> dict:
    """原始数据区的布局配置（左列 N/O/P、右列 Q/R/S）。"""
    return {
        "date_value_cell": "O8",
        "head_row": 9,
        "left": _raw_entries(RAW_LEFT_POINTS, 1, 14, 15, 16),
        "right": _raw_entries(RAW_RIGHT_POINTS, RAW_LEFT_POINTS + 1, 17, 18, 19),
    }


def _add_raw_grid(ws) -> dict:
    """造"原始数据区"（照抄真实母版）：左列 6 块、右列 2 块，**右列下面预留带边框的空格**。

    真实母版里右列 row 10~27 全都有细边框，只是没填内容 —— 这正是"12 个点塞得下、
    不用下移任何东西"的物理基础。
    """
    ws.cell(row=8, column=14, value="日期")
    ws.cell(row=8, column=15, value="2026-07-30")
    for c, v in ((14, "点号"), (15, "坐标数据"), (17, "点号"), (18, "坐标数据")):
        ws.cell(row=9, column=c, value=v)
    # 整个网格（含空位）都带边框
    for r in range(RAW_TOP, RAW_BOTTOM + 1):
        for c in range(14, 20):
            ws.cell(row=r, column=c).border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

    for i in range(RAW_LEFT_POINTS):                 # 左列 6 块
        r = RAW_TOP + i * 3
        ws.cell(row=r, column=14, value=f"JJC{i + 1}")
        for j, comp in enumerate(("X", "Y", "Z")):
            ws.cell(row=r + j, column=15, value=comp)
            ws.cell(row=r + j, column=16, value=3412966.0 + i + j / 1000)
    for i in range(RAW_RIGHT_POINTS):                # 右列 2 块（下面留空）
        r = RAW_TOP + i * 3
        ws.cell(row=r, column=17, value=f"JJC{RAW_LEFT_POINTS + 1 + i}")
        for j, comp in enumerate(("X", "Y", "Z")):
            ws.cell(row=r + j, column=18, value=comp)
            ws.cell(row=r + j, column=19, value=3412966.0 + i + j / 1000)
    return _raw_config()


def _build_master(path: Path, points: int = TEMPLATE_POINTS) -> dict:
    """造一份与真实母版同构的样表：8 个点位行 + 预留空白行 + 说明/结论/签字。"""
    wb = Workbook()
    ws = wb.active
    ws.title = SETTLE
    ws["A1"] = "沉降监测报表"
    ws["A5"] = "监测点号"
    for i in range(1, points + 1):
        r = 6 + i
        ws.cell(row=r, column=1, value=f"JJC{i}")
        ws.cell(row=r, column=2, value=3.5)
        ws.cell(row=r, column=10, value=30)          # I 控制累计
        ws.cell(row=r, column=11, value=10)          # J 控制速率
        _bordered(ws, r)
    for r in range(7 + points, 7 + points + 12):     # 预留的空白点位行（只有控制值）
        ws.cell(row=r, column=10, value=30)
        ws.cell(row=r, column=11, value=10)
    tail = 7 + points + 12
    ws.cell(row=tail, column=1, value=TAIL_TEXT)
    ws.cell(row=tail + 4, column=1, value=CONCLUSION_TEXT)
    ws.cell(row=tail + 8, column=1, value=SIGN_TEXT)
    ws.merge_cells(start_row=tail + 8, start_column=1, end_row=tail + 8, end_column=4)  # 签字栏合并

    ws2 = wb.create_sheet(DISP)
    ws2["A5"] = "监测点号"
    for i in range(1, points + 1):
        r = 7 + (i - 1) * 2
        ws2.cell(row=r, column=1, value=f"JJC{i}")
        ws2.cell(row=r, column=2, value=3412966.0)
        ws2.cell(row=r + 1, column=2, value=510239.0)
        for rr in (r, r + 1):
            ws2.cell(row=rr, column=10, value=20)    # I 控制累计
            ws2.cell(row=rr, column=11, value=3)     # J 控制速率
            _bordered(ws2, rr)
        ws2.merge_cells(start_row=r, start_column=1, end_row=r + 1, end_column=1)  # 点号跨两行
    last = 7 + (points - 1) * 2 + 1
    for r in range(last + 1, last + 5):              # 预留 4 行 = 2 个点位块
        ws2.cell(row=r, column=10, value=20)
        ws2.cell(row=r, column=11, value=3)
    tail2 = last + 5
    ws2.cell(row=tail2, column=1, value=TAIL_TEXT)
    ws2.cell(row=tail2 + 3, column=1, value=CONCLUSION_TEXT)
    ws2.cell(row=tail2 + 7, column=1, value=SIGN_TEXT)
    # 签字栏也做一处跨列合并：下移时必须一起搬走（openpyxl 的 insert_rows 不搬合并区）
    ws2.merge_cells(start_row=tail2 + 7, start_column=1, end_row=tail2 + 7, end_column=4)
    # 原始数据区只挂在「竖向位移」表上（与真实母版一致）
    lay = _layout()
    lay["sheets"][0]["raw_data"] = _add_raw_grid(ws)
    wb.save(path)
    return lay


def _mk_project(client, n_points: int) -> int:
    """建项目 + n 个测点（both）+ 两期数据（09-01 初始 / 09-02 本期）。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ-EXP", "name": "导出补点测试"}).json()
    for i in range(1, n_points + 1):
        pt = client.post(f"/api/v1/projects/{p['id']}/points",
                         json={"point_no": f"JJC{i}", "point_type": "both"}).json()
        for obs_date, bump in (("2026-09-01", 0.0), ("2026-09-02", 0.001)):
            for st, comp, base in (("settlement", "value", 3.5),
                                   ("displacement", "X", 3412966.0),
                                   ("displacement", "Y", 510239.0)):
                client.post(
                    f"/api/v1/points/{pt['id']}/records",
                    json={"series_type": st, "component": comp, "obs_date": obs_date,
                          "value_mm": (base + bump + i * 0.01) * 1000},
                )
    return p["id"]


def _mk_template(project_id: int, path: Path, layout: dict, name: str = "八点母版") -> int:
    with SessionLocal() as db:
        # layout_json 是 JSON 列，直接存 dict（存成字符串的话导出侧会 .get() 报错）
        tpl = ExportTemplate(project_id=project_id, name=name, template_type="excel",
                             is_builtin=False, file_path=str(path), layout_json=layout)
        db.add(tpl)
        db.commit()
        db.refresh(tpl)
        return tpl.id


def _export(client, project_id: int, template_id: int):
    r = client.get(f"/api/v1/projects/{project_id}/reports/excel",
                   params={"template_id": template_id})
    assert r.status_code == 200, r.text
    # 后端把头写成 URL 编码的 JSON（HTTP 头不能带非 ASCII）
    summary = json.loads(unquote(r.headers.get("x-export-summary", "{}") or "{}"))
    return load_workbook(BytesIO(r.content)), summary


def _col_a(ws) -> list[tuple[int, str]]:
    return [(r, str(ws.cell(row=r, column=1).value)) for r in range(1, ws.max_row + 1)
            if ws.cell(row=r, column=1).value]


def _row_of(ws, text: str) -> int | None:
    for r, v in _col_a(ws):
        if text in v:
            return r
    return None


def _master_file(name: str, points: int = TEMPLATE_POINTS) -> Path:
    path = Path(settings.templates_dir) / f"{name}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先落文件再让 openpyxl 写入（_build_master 会覆盖）
    _build_master(path, points)
    return path


# ────────────────────── 1. 自动补点（核心） ──────────────────────


def test_extra_points_are_appended_to_settlement_sheet(client):
    """项目 12 个点、母版 8 个点位 → 竖向位移表必须出现 JJC9~JJC12（原来整条丢）。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout())

    wb, summary = _export(client, pid, tid)
    ws = wb[SETTLE]
    got = [v for r, v in _col_a(ws) if v.startswith("JJC")]
    assert got == [f"JJC{i}" for i in range(1, 13)], f"竖向位移点号不对：{got}"
    assert summary["added_points"][SETTLE] == ["JJC9", "JJC10", "JJC11", "JJC12"]


def test_extra_points_are_appended_to_displacement_sheet(client):
    """位移表每点占 X/Y 两行，补点时两行都要有，且点号跨两行合并。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout())

    wb, summary = _export(client, pid, tid)
    ws = wb[DISP]
    x_rows = [(r, v) for r, v in _col_a(ws) if v.startswith("JJC")]
    assert [v for _, v in x_rows] == [f"JJC{i}" for i in range(1, 13)], f"位移表点号不对：{x_rows}"
    # X 行与 Y 行间隔 2；最后一个点落在第 7+11*2 = 29 行
    assert [r for r, _ in x_rows] == list(range(7, 30, 2))
    merges = {str(m) for m in ws.merged_cells.ranges}
    assert "A27:A28" in merges and "A29:A30" in merges, (
        f"补出来的点位块没有把点号列跨两行合并（母版惯例）：{sorted(merges)}"
    )


def test_new_rows_keep_template_format_and_control_values(client):
    """补出来的行要长得像母版那一行：I/J 控制值有值、行高一致、边框不丢。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout())

    wb, _ = _export(client, pid, tid)
    for sheet, new_rows, ctrl in ((SETTLE, [15, 16, 17, 18], (30, 10)),
                                  (DISP, list(range(23, 31)), (20, 3))):
        ws = wb[sheet]
        for r in new_rows:
            assert ws.cell(row=r, column=10).value == ctrl[0], f"{sheet} 行{r} 控制累计丢失"
            assert ws.cell(row=r, column=11).value == ctrl[1], f"{sheet} 行{r} 控制速率丢失"
            a = ws.cell(row=r, column=1)
            assert a.border.left.style or a.border.bottom.style, (
                f"{sheet} 行{r} 边框丢了（合并格的样式在左上角，复制时要回到左上角取）"
            )
        # 行高：拿最后一行模板点位行做基准
        proto = 14 if sheet == SETTLE else 22
        h = ws.row_dimensions[proto].height
        if h is not None:
            for r in new_rows:
                assert ws.row_dimensions[r].height == h, f"{sheet} 行{r} 行高没跟上母版"


# ────────────────────── 2. 下移说明行 ──────────────────────


def test_tail_block_moves_down_when_no_room(client):
    """位移表只有 4 行空位（2 个点位块），补 4 个点要往后挤 —— 说明/结论/签字必须整块下移且内容不丢。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout())

    wb, summary = _export(client, pid, tid)
    ws = wb[DISP]
    last_point_row = 29
    assert summary["shifted_rows"][DISP] == 4

    tail, concl, sign = (_row_of(ws, TAIL_TEXT), _row_of(ws, CONCLUSION_TEXT), _row_of(ws, SIGN_TEXT))
    assert tail and concl and sign, "下移之后说明/结论/签字有丢失"
    assert last_point_row < tail < concl < sign, (
        f"下移后顺序不对：数据区到 {last_point_row}，说明 {tail}、结论 {concl}、签字 {sign}"
    )
    # 签字行的合并区要跟着搬（A34:D34 → A38:D38）
    merges = {str(m) for m in ws.merged_cells.ranges}
    assert f"A{sign}:D{sign}" in merges, f"签字栏合并没有跟着下移：{sorted(merges)}"


def test_settlement_sheet_does_not_shift_when_spare_rows_enough(client):
    """竖向位移表有 12 行空位，补 4 个点够用 —— 不该白挪一下（挪了说明行位置就变了）。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout())

    wb, summary = _export(client, pid, tid)
    assert SETTLE not in (summary["shifted_rows"] or {})
    assert _row_of(wb[SETTLE], TAIL_TEXT) == 27, "竖向位移的说明行不该动"


# ────────────────────── 2.5 原始数据区（X/Y/Z 坐标小表）补块 ──────────────────────
#
# 用户第二次报障（截图）：「这个没有跟上?」—— 数据表补好了，但右边的原始数据区
# 仍然只有 JJC1~JJC8，JJC9~JJC12 整条缺失。
# 真实母版里右列 row 10~27 是**预留好带边框的空格**（只填了 JJC7/JJC8），
# 12 个测点（左 6 + 右 6）正好塞得下，所以这一区**不需要下移任何东西**。


def _raw_blocks(ws) -> dict[str, int]:
    """原始数据区里的 点号→首行行号（左列点号在 N、右列在 Q）。"""
    out: dict[str, int] = {}
    for r in range(RAW_TOP, RAW_BOTTOM + 1):
        for c in (14, 17):
            v = ws.cell(row=r, column=c).value
            if v and str(v).startswith("JJC"):
                out[str(v)] = r
    return out


def test_raw_grid_slots_beyond_project_points_are_cleared(client):
    """项目 6 个点、母版原始数据区 8 块 → 多出来的槽位要**连点号一起清掉**。

    模板可跨项目共用之后，母版里的点号很可能是**别的基坑**的；照抄会让报表印出
    JJC7/JJC8 两个空坐标的点位 —— 读表的人会当成"这两个点缺测了"，
    比一行空白更容易误判（2026-10-08 与数据表同一口径的变更）。
    """
    path = _master_file("m_master")
    pid = _mk_project(client, 6)
    tid = _mk_template(pid, path, _layout(with_raw=True))

    wb, _ = _export(client, pid, tid)
    assert sorted(_raw_blocks(wb[SETTLE])) == [f"JJC{i}" for i in range(1, 7)]


def test_raw_grid_extra_points_go_to_right_column(client):
    """项目 12 个点、母版原始数据区 8 个块 → JJC9~JJC12 必须补到右列下方的空格里。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout(with_raw=True))

    wb, summary = _export(client, pid, tid)
    ws = wb[SETTLE]
    blocks = _raw_blocks(ws)
    expected = {f"JJC{i}": RAW_TOP + ((i - 1) % 6) * 3 for i in range(1, 13)}
    assert blocks == expected, f"原始数据区点位块不对：{sorted(blocks.items())}"
    assert summary["added_raw_points"] == {SETTLE: ["JJC9", "JJC10", "JJC11", "JJC12"]}
    assert not (summary.get("missing_raw_points") or {}), "12 个点塞得下，不该报放不下"
    # 补在网格内部 → 说明行不动（与数据表那边「有空位就不下移」同一个原则）
    assert _row_of(ws, TAIL_TEXT) == 27, "补原始数据区不该挪动说明行"


def test_raw_blocks_get_component_labels_and_values(client):
    """新补的块要写全：点号 + X/Y/Z 分量文字 + 三个坐标值。

    母版里 X/Y/Z 是**母版自带的文字**，原实现只写坐标值列 ——
    新块不补分量列的话，就是"一列数字、不知道哪行是 X"。
    """
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout(with_raw=True))

    wb, _ = _export(client, pid, tid)
    ws = wb[SETTLE]
    for row, comp in ((16, "X"), (17, "Y"), (18, "Z")):      # JJC9 那一块
        assert ws.cell(row=row, column=18).value == comp, f"第{row}行分量列没写 {comp}"
        v = ws.cell(row=row, column=19).value
        assert isinstance(v, (int, float)) and v > 3, f"第{row}行坐标值没写：{v!r}"


def test_raw_grid_overflow_is_reported_not_silently_dropped(client):
    """两列网格（左 6 + 右 6 = 12 个位）真的排满了，放不下的点必须报出来 —— 绝不静默丢。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 16)
    tid = _mk_template(pid, path, _layout(with_raw=True))

    wb, summary = _export(client, pid, tid)
    assert summary["added_raw_points"] == {SETTLE: ["JJC9", "JJC10", "JJC11", "JJC12"]}
    assert summary["missing_raw_points"] == {SETTLE: ["JJC13", "JJC14", "JJC15", "JJC16"]}, (
        f"网格放不下的点没报出来：{summary.get('missing_raw_points')}"
    )
    assert set(_raw_blocks(wb[SETTLE])) == {f"JJC{i}" for i in range(1, 13)}


def test_injection_without_raw_expand_really_disappears(client, monkeypatch):
    """注入回归自测：关掉原始数据区补块（= 修复前的行为），JJC9~JJC12 必须真的整条不见。"""
    from backend.reports import master_expand

    # 注意：fill_raw_data_blocks 里是函数内 `from ... import plan_raw_slots`，
    # 所以要打补丁到**定义处**的模块属性上，打在 master_report 上无效。
    monkeypatch.setattr(master_expand, "plan_raw_slots", lambda *a, **k: [])
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout(with_raw=True))

    wb, summary = _export(client, pid, tid)
    assert sorted(_raw_blocks(wb[SETTLE])) == [f"JJC{i}" for i in range(1, 9)], (
        "关掉补块之后原始数据区居然还是 12 个点 —— 说明这条断言测不到补块逻辑"
    )
    assert not (summary.get("added_raw_points") or {})


# ────────────────────── 3. 母版点位比项目多 → 保留空行 ──────────────────────


def test_template_with_more_points_keeps_blank_rows_without_foreign_labels(client):
    """项目只有 6 个点、母版有 8 个 → **空行保留，但母版多出来的点号要清掉**。

    2026-10-08 变更（模板独立化的前提之一）：模板现在可以**跨项目共用**，
    母版里的点号很可能是别的基坑的。原来"点号照写"的规则在共用场景下会让报表
    印出 JJC7/JJC8 两个**没有数据的点位**——读表的人会以为这两个点缺测了，
    比一行空白更容易误判。新规则：本项目有的点号保留（当日缺测也不清），
    本项目没有的点号连标签一起清掉，**行本身保留**（版式的空行语义不变）。
    """
    path = _master_file("m_master")
    pid = _mk_project(client, 6)
    tid = _mk_template(pid, path, _layout())

    wb, summary = _export(client, pid, tid)
    ws = wb[SETTLE]
    got = [v for r, v in _col_a(ws) if v.startswith("JJC")]
    assert got == [f"JJC{i}" for i in range(1, 7)], f"不属于本项目的点号不该留在报表上：{got}"
    for r in (13, 14):                     # 母版里 JJC7 / JJC8 那两行
        assert ws.cell(row=r, column=1).value in (None, ""), "别的基坑的点号必须清掉"
        assert ws.cell(row=r, column=2).value is None, "没数据的点不该带出母版旧值"
    assert not (summary.get("added_points") or {}), "没有多出来的点，不该报补点"
    assert not (summary.get("added_raw_points") or {}), "没有多出来的点，原始数据区也不该补块"


# ────────────────────── 4. 提示信息（响应头） ──────────────────────


def test_export_summary_is_reported_in_header(client):
    """导出摘要要带出来，界面才能说清"为什么比母版多了几行"。"""
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout())

    _, summary = _export(client, pid, tid)
    assert summary["template_points"] == {SETTLE: 8, DISP: 8}
    assert summary["added_points"] == {SETTLE: ["JJC9", "JJC10", "JJC11", "JJC12"],
                                       DISP: ["JJC9", "JJC10", "JJC11", "JJC12"]}
    assert summary["shifted_rows"] == {DISP: 4}


# ────────────────────── 5. 源码守卫 ──────────────────────


def test_injection_without_expand_points_really_disappear(client, monkeypatch):
    """注入回归自测：把补点关掉（= 修复前的行为），JJC9~JJC12 必须真的整条不见。

    证明上面几条断言不是摆设 —— 它们抓的正是用户报的那个现象。
    """
    from backend.reports import master_report

    monkeypatch.setattr(
        master_report, "_expand_blocks",
        lambda ws, f, kind, blocks, db, project_id: (blocks, [], 0),
    )
    path = _master_file("m_master")
    pid = _mk_project(client, 12)
    tid = _mk_template(pid, path, _layout())

    wb, summary = _export(client, pid, tid)
    got = [v for _, v in _col_a(wb[SETTLE]) if v.startswith("JJC")]
    assert got == [f"JJC{i}" for i in range(1, 9)], (
        f"关掉补点之后居然还是导出了 12 个点（{got}）—— 说明这些测试根本测不到补点逻辑"
    )
    assert not (summary.get("added_points") or {})


def test_fill_data_sheets_always_goes_through_expand():
    """源码守卫：数据表填充必须走补点逻辑，不能再回到"只按母版行位写"的老路。"""
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parent.parent
           / "backend" / "reports" / "master_report.py").read_text(encoding="utf-8")
    assert "_expand_blocks(ws, f, kind, blocks" in src, (
        "fill_data_sheets 没有接自动补点（补点函数写了但没人调用）"
    )
    assert "plan_raw_slots(" in src, (
        "原始数据区（X/Y/Z 坐标小表）没有接自动补块 —— JJC9~JJC12 又会在那一区整条消失"
    )
    expand = (_P(__file__).resolve().parent.parent
              / "backend" / "reports" / "master_expand.py").read_text(encoding="utf-8")
    assert "merge_cells" in expand and "unmerge_cells" in expand, (
        "搬行必须自己处理合并单元格（openpyxl 的 insert_rows 不搬）"
    )


# ────────────────────── 数值格式（2026-10-08 用户反馈"会有小数点"） ──────────────────────


def test_raw_grid_numbers_use_general_format(client):
    """原始数据区（X/Y/Z 坐标小表）的数值不能带母版残留的 5 位小数格式。

    用户 2026-10-08 反馈：「你改完后没有顺手把单元格宽度控制在最佳位置 我看了会有小数点呢」。
    实测根因：母版是从真实报表另存来的，Excel 给数值格自动挂了 `0.00000_ ` 格式，
    系统只改值不改格式 → 坐标 3412966.0 显示成 3412966.00000、控制值 20 显示成 20.00000。
    现在写数值的格子一律设成「常规」。
    """
    pid = _mk_project(client, 8)
    path = Path(settings.templates_dir) / "原始数据区格式母版.xlsx"
    lay = _build_master(path, 8)

    # 给母版原始数据区的坐标格挂上真实母版那种残留格式
    wb = load_workbook(path)
    ws = wb[SETTLE]
    for r in range(RAW_TOP, RAW_BOTTOM + 1):
        for c in (16, 19):            # 左列/右列的坐标值列
            ws.cell(row=r, column=c).number_format = "0.00000_ "
    wb.save(path)
    wb.close()

    tid = _mk_template(pid, path, lay)
    out, _ = _export(client, pid, tid)
    wso = out[SETTLE]

    checked = 0
    for r in range(RAW_TOP, RAW_BOTTOM + 1):
        for c in (16, 19):
            cell = wso.cell(row=r, column=c)
            if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                assert cell.number_format == "General", (
                    f"原始数据区 {r}行{c}列 还留着老格式 {cell.number_format}（值 {cell.value}）"
                )
                checked += 1
    assert checked >= 6, f"原始数据区没读到数值格（命中 {checked}），这条测试等于没写"
    out.close()
