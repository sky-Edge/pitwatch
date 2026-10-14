# -*- coding: utf-8 -*-
"""导入时「按用户指定的观测日期重新核对」测试。

背景：Excel 里读到日期后原先不能再改。而现场台账的日期经常需要改
（表里写错、或者「初始值清单」根本没写日期）。改日期不是改个字段那么简单 ——
**换个日期，"同一天有没有已有记录"就变了**，有效行/冲突行必须重算，
否则用户看到的是错的条数，导入时才发现少了几条。
"""
import io
from pathlib import Path

import openpyxl
import pytest

from backend.core.config import settings
from tests.test_import_parser import _make_wb

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _wb_bytes(wb: openpyxl.Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _make_wb_without_date() -> openpyxl.Workbook:
    """把「本次监测时间」那行换成不含日期的文字 —— 模拟「初始值清单」这类没写日期的表。"""
    wb = _make_wb()
    for name in ("竖向位移", "r.立柱结构水平位移"):
        wb[name].cell(row=3, column=1, value="仪器型号：Leica TZ05")
    return wb


def _setup(client):
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "八号基坑"}).json()
    pid = p["id"]
    for no in ("JJC1", "JJC2"):
        client.post(f"/api/v1/projects/{pid}/points", json={"point_no": no, "point_type": "both"})
    return pid


def _upload(client, pid, wb=None, name="八号基坑（第一期）2026.7.31.xlsx"):
    return client.post(
        f"/api/v1/projects/{pid}/import/upload",
        files={"file": (name, _wb_bytes(wb or _make_wb()), XLSX_MIME)},
    ).json()


def _recheck(client, batch_id: int, dates: dict[str, str | None]):
    return client.post(
        f"/api/v1/import/batches/{batch_id}/recheck",
        json={"dates": [{"sheet_name": k, "obs_date": v} for k, v in dates.items()]},
    )


# ---------- 有日期的表也能改 ----------


def test_row_carries_sheet_name(client):
    """预览行要带 sheet_name：按表改日期时，前端靠它把新日期回填到该表的行上。"""
    pid = _setup(client)
    data = _upload(client, pid)
    settle = [r for r in data["valid_rows"] if r["series_type"] == "settlement"]
    disp = [r for r in data["valid_rows"] if r["series_type"] == "displacement"]
    assert settle and disp
    assert {r["sheet_name"] for r in settle} == {"竖向位移"}
    assert {r["sheet_name"] for r in disp} == {"r.立柱结构水平位移"}


def test_sheets_carry_original_date(client):
    """sheets 要同时给出「当前日期」与「表里原本写的日期」，前端才能显示"已改"。"""
    pid = _setup(client)
    data = _upload(client, pid)
    assert all(s["obs_date"] == "2026-07-31" for s in data["sheets"])
    assert all(s["original_obs_date"] == "2026-07-31" for s in data["sheets"])

    after = _recheck(client, data["batch_id"], {"竖向位移": "2026-07-25"}).json()
    settle_sheet = next(s for s in after["sheets"] if s["sheet_name"] == "竖向位移")
    other = next(s for s in after["sheets"] if s["sheet_name"] != "竖向位移")
    assert settle_sheet["obs_date"] == "2026-07-25"
    assert settle_sheet["original_obs_date"] == "2026-07-31"  # 表里的原值仍要看得见
    assert other["obs_date"] == "2026-07-31"  # 没改的表不受影响


def test_recheck_only_changes_target_sheet(client):
    """只改一张表的日期，另一张表的行不能跟着变 —— 逐表覆盖要精确。"""
    pid = _setup(client)
    data = _upload(client, pid)
    after = _recheck(client, data["batch_id"], {"竖向位移": "2026-08-01"}).json()
    settle_dates = {r["obs_date"] for r in after["valid_rows"] if r["series_type"] == "settlement"}
    disp_dates = {r["obs_date"] for r in after["valid_rows"] if r["series_type"] == "displacement"}
    assert settle_dates == {"2026-08-01"}
    assert disp_dates == {"2026-07-31"}


def test_recheck_into_occupied_date_creates_conflict(client):
    """把日期改到一个已有记录的日期上 → 那几行必须变成冲突（跳过，不覆盖）。"""
    pid = _setup(client)
    data = _upload(client, pid)
    pts = client.get(f"/api/v1/projects/{pid}/points").json()
    jjc1 = next(x for x in pts if x["point_no"] == "JJC1")
    # 2026-07-25 已有一条手工录入的 JJC1 沉降
    client.post(
        f"/api/v1/points/{jjc1['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-25", "value_mm": 4900.0},
    )
    # 改前：07-31 上没有任何冲突
    assert data["conflict_rows"] == []

    after = _recheck(
        client, data["batch_id"], {"竖向位移": "2026-07-25", "r.立柱结构水平位移": "2026-07-25"}
    ).json()
    conflicts = {(r["point_no"], r["series_type"]) for r in after["conflict_rows"]}
    assert ("JJC1", "settlement") in conflicts
    assert after["conflict_rows"][0]["existing_value_mm"] == 4900.0
    assert len(after["valid_rows"]) == 5  # 6 - 1


def test_recheck_off_occupied_date_frees_rows(client):
    """反向：原来冲突的日期改开后，那些行要回到「可导入」——不能沿用旧分组。"""
    pid = _setup(client)
    pts = client.get(f"/api/v1/projects/{pid}/points").json()
    jjc1 = next(x for x in pts if x["point_no"] == "JJC1")
    client.post(
        f"/api/v1/points/{jjc1['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 4900.0},
    )
    data = _upload(client, pid)
    assert ("JJC1", "settlement") in {(r["point_no"], r["series_type"]) for r in data["conflict_rows"]}

    after = _recheck(
        client, data["batch_id"], {"竖向位移": "2026-07-20", "r.立柱结构水平位移": "2026-07-20"}
    ).json()
    assert after["conflict_rows"] == []
    assert len(after["valid_rows"]) == 6
    assert {r["obs_date"] for r in after["valid_rows"]} == {"2026-07-20"}


def test_recheck_clearing_date_makes_rows_undated(client):
    """把日期清空 → 那些行变成"待指定日期"，不会带着一个编造的日期入库。"""
    pid = _setup(client)
    data = _upload(client, pid)
    after = _recheck(client, data["batch_id"], {"竖向位移": None}).json()
    undated = {r["sheet_name"] for r in after["no_date_rows"]}
    assert "竖向位移" in undated
    assert all(r["obs_date"] is None for r in after["no_date_rows"])
    # 另一张表照旧
    assert len(after["valid_rows"]) == 4


def test_recheck_unchanged_dates_is_stable(client):
    """不改日期重新核对：结果应与首次一致（防重算引入偏差）。"""
    pid = _setup(client)
    first = _upload(client, pid)
    again = _recheck(client, first["batch_id"], {"竖向位移": "2026-07-31"}).json()
    assert len(again["valid_rows"]) == len(first["valid_rows"]) == 6
    assert again["conflict_rows"] == []


# ---------- 没写日期的表：指定「初测日期」后能导入 ----------


def test_undated_sheet_then_assign_date_and_import(client):
    """表里没写日期（初始值清单）→ 指定初测日期 → 重新核对 → 导入落到该日期。"""
    pid = _setup(client)
    data = _upload(client, pid, _make_wb_without_date())
    assert data["valid_rows"] == []
    assert len(data["no_date_rows"]) == 6
    assert all(s["obs_date"] is None for s in data["sheets"])
    assert all(s["original_obs_date"] is None for s in data["sheets"])

    batch_id = data["batch_id"]
    after = _recheck(
        client,
        batch_id,
        {"竖向位移": "2026-07-01", "r.立柱结构水平位移": "2026-07-01"},
    ).json()
    assert after["no_date_rows"] == []
    assert len(after["valid_rows"]) == 6
    assert {r["obs_date"] for r in after["valid_rows"]} == {"2026-07-01"}

    conf = client.post(f"/api/v1/import/batches/{batch_id}/confirm", json={"valid_rows": after["valid_rows"]})
    assert conf.json()["imported_rows"] == 6

    pts = client.get(f"/api/v1/projects/{pid}/points").json()
    jjc1 = next(x for x in pts if x["point_no"] == "JJC1")
    recs = client.get(f"/api/v1/points/{jjc1['id']}/records").json()
    assert recs and all(r["obs_date"] == "2026-07-01" for r in recs)


def test_undated_rows_not_imported_without_date(client):
    """没指定日期的行不能入库（哪怕前端硬塞进来），且要计数报告、不能静默丢。"""
    pid = _setup(client)
    data = _upload(client, pid, _make_wb_without_date())
    rows = [dict(r, obs_date=None) for r in data["no_date_rows"]]
    conf = client.post(f"/api/v1/import/batches/{data['batch_id']}/confirm", json={"valid_rows": rows})
    body = conf.json()
    assert body["imported_rows"] == 0
    assert body["skipped_no_date"] == len(rows)


# ---------- 边界与防呆 ----------


def test_recheck_rejected_after_confirm(client):
    """已确认的批次不能再改日期（改预览没有意义，还会造成"看起来导入的是新日期"）。"""
    pid = _setup(client)
    data = _upload(client, pid)
    client.post(f"/api/v1/import/batches/{data['batch_id']}/confirm", json={"valid_rows": data["valid_rows"]})
    resp = _recheck(client, data["batch_id"], {"竖向位移": "2026-07-25"})
    assert resp.status_code == 400
    assert "不可再修改日期" in resp.json()["detail"]


def test_recheck_missing_original_file(client):
    """原始文件不在（被清理/换机器）→ 409 + 人话提示，而不是 500。"""
    pid = _setup(client)
    data = _upload(client, pid)
    for f in settings.upload_dir.glob("*.xlsx"):
        f.unlink()
    resp = _recheck(client, data["batch_id"], {"竖向位移": "2026-07-25"})
    assert resp.status_code == 409
    assert "重新上传" in resp.json()["detail"]


def test_recheck_bad_date_format(client):
    """脏日期必须在入口挡住：放进去会算出错误的冲突判定。"""
    pid = _setup(client)
    data = _upload(client, pid)
    resp = _recheck(client, data["batch_id"], {"竖向位移": "2026/07/25"})
    assert resp.status_code == 400
    assert "格式不正确" in resp.json()["detail"]


def test_recheck_batch_not_found(client):
    assert _recheck(client, 99999, {"竖向位移": "2026-07-25"}).status_code == 404


# ---------- 源码守卫：防止这两个入口被改回"只读日期" ----------


def test_frontend_uses_recheck_api() -> None:
    import_page = (SRC / "pages" / "ImportPage.tsx").read_text(encoding="utf-8")
    client_ts = (SRC / "api" / "client.ts").read_text(encoding="utf-8")
    assert "importApi.recheck" in import_page, "导入页没有调用重新核对接口，改日期不会重算"
    assert "/recheck" in client_ts, "前端没有 recheck 接口定义"


def test_date_editor_not_disabled_for_dated_sheets() -> None:
    """有日期的表也必须能改：日期选择器不能只在"没日期"时才渲染。"""
    import_page = (SRC / "pages" / "ImportPage.tsx").read_text(encoding="utf-8")
    assert "renderSheetDate" in import_page
    # 每个数据表都渲染选择器（不是只在 needs_date 时才给）
    assert "curPreview.sheets.map((s) => (" in import_page
    # 旧的"只有没日期才让选"的补填字段必须退役
    assert "noDateValue" not in import_page, "旧的补填日期字段还在，说明流程没统一"
    assert "noDateInclude" not in import_page


def test_backend_exposes_recheck_route() -> None:
    src = (
        Path(__file__).resolve().parent.parent / "backend" / "api" / "v1" / "imports.py"
    ).read_text(encoding="utf-8")
    assert '@batch_router.post("/{batch_id}/recheck"' in src
    assert "recheck_with_dates" in src


def test_upload_records_file_path() -> None:
    """改日期要重解析原文件，所以上传时必须把路径记进批次 —— 否则功能会静默失效。"""
    src = (
        Path(__file__).resolve().parent.parent / "backend" / "api" / "v1" / "imports.py"
    ).read_text(encoding="utf-8")
    assert '"file_path": str(path)' in src
