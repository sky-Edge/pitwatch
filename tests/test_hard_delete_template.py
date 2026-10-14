# -*- coding: utf-8 -*-
"""回归：项目含自定义导出模板时 hard delete 不再 500（FK constraint）。"""
import io

import pytest
from openpyxl import Workbook

from backend.core.config import settings


@pytest.fixture(autouse=True)
def _cleanup():
    settings.ensure_dirs()
    for f in settings.backup_dir.glob("*.db"):
        f.unlink(missing_ok=True)
    yield
    # 清理模板残留目录
    import shutil

    tpl_root = settings.templates_dir
    if tpl_root.exists():
        for d in tpl_root.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def _tpl_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "竖向位移"
    ws["A5"] = "监测\n点号"
    ws["B5"] = "初始值（m)"
    ws["D5"] = "本次测值（m）"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_hard_delete_project_with_uploaded_template(client):
    """上传模板后再物理删除项目 → 200（曾因 export_templates 外键失败 500）。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "模板删除回归"}).json()
    pid = p["id"]
    pt = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "JJC1", "point_type": "settlement"}).json()
    client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    # 上传自定义模板（触发 export_templates 行 project_id=pid）
    r = client.post(
        f"/api/v1/projects/{pid}/export-templates/upload",
        data={"name": "报送版"},
        files={"file": ("t.xlsx", _tpl_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200

    # 物理删除项目
    resp = client.delete(f"/api/v1/projects/{pid}/hard", params={"force": "PRJ001"})
    assert resp.status_code == 200, f"删除失败: {resp.text}"
    assert resp.json()["deleted"] is True
    # 项目与模板记录都清除
    assert client.get(f"/api/v1/projects/{pid}").status_code == 404
    lst = client.get(f"/api/v1/projects/{pid}/export-templates").status_code == 404
