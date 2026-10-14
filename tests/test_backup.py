# -*- coding: utf-8 -*-
"""Phase 9 测试：备份 / 还原。"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.core.config import settings


@pytest.fixture(autouse=True)
def _clean_backup_dir():
    """测试前清空备份目录，保证备份文件隔离。

    ⚠ 这个 fixture 在删东西，所以删之前必须先确认目录不是生产目录。
    （历史上 `backup_dir` 硬编码指向 `database/backups`，于是每跑一次测试
    就删掉用户的真实备份 —— 见 `tests/conftest.py::_guard_against_prod_paths`
    里的事故说明。`test_hard_delete*.py` / `test_master_report.py` 有同样的清理逻辑。）
    """
    assert _under_temp(settings.backup_dir), (
        f"备份目录不在临时目录下（{settings.backup_dir}），拒绝清理，以免删掉真实备份"
    )
    settings.ensure_dirs()
    for f in settings.backup_dir.glob("*.db"):
        f.unlink(missing_ok=True)
    yield
    for f in settings.backup_dir.glob("*.db"):
        f.unlink(missing_ok=True)


def _under_temp(p: Path) -> bool:
    """p 是否位于系统临时目录之下（测试目录必须如此）。"""
    try:
        return Path(tempfile.gettempdir()).resolve() in p.resolve().parents
    except OSError:
        return False


def test_test_dirs_are_isolated_from_production():
    """显式记录：测试用的备份目录与模板目录都必须在临时目录下。

    少了这条隔离，`test_backup.py` / `test_hard_delete*.py` / `test_master_report.py`
    的清理逻辑就会删掉用户的真实备份；
    `test_hard_delete_template.py` 还会 rmtree 掉用户上传的模板目录。
    """
    assert _under_temp(settings.backup_dir), f"备份目录未隔离：{settings.backup_dir}"
    assert _under_temp(settings.templates_dir), f"模板目录未隔离：{settings.templates_dir}"
    assert _under_temp(settings.upload_dir), f"上传目录未隔离：{settings.upload_dir}"
    assert _under_temp(_db_file()), f"数据库未隔离：{_db_file()}"


def _db_file() -> Path:
    """conftest 中 MONITOR_DB_URL 指向的临时库文件。"""
    url = settings.database_url
    assert url.startswith("sqlite:///")
    return Path(url.removeprefix("sqlite:///"))


def _seed(client):
    """建一个项目 + 一条观测记录作为种子数据。"""
    p = client.post("/api/v1/projects", json={"code": "PRJ001", "name": "备份测试"}).json()
    pid = p["id"]
    pt = client.post(f"/api/v1/projects/{pid}/points", json={"point_no": "P1", "point_type": "settlement"}).json()
    client.post(
        f"/api/v1/points/{pt['id']}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 4907.0},
    )
    return pid


def test_create_backup_and_list(client):
    """手动备份：生成快照文件 + 审计记录。"""
    pid = _seed(client)
    resp = client.post("/api/v1/backup")
    assert resp.status_code == 200
    rec = resp.json()
    assert rec["filename"].startswith("monitor_backup_")
    assert rec["filename"].endswith(".db")
    assert rec["size_bytes"] > 0

    # 备份文件确实存在于 backup 目录
    f = settings.backup_dir / rec["filename"]
    assert f.exists()

    # 快照内容包含种子项目
    con = sqlite3.connect(f)
    row = con.execute("SELECT code FROM projects WHERE id=?", (pid,)).fetchone()
    con.close()
    assert row == ("PRJ001",)

    lst = client.get("/api/v1/backup/list").json()
    assert len(lst) >= 1


def test_restore_requires_confirm(client):
    """还原必须有 confirm=true，否则 400。"""
    pid = _seed(client)
    rec = client.post("/api/v1/backup").json()
    # 无 confirm
    r = client.post(f"/api/v1/backup/{rec['id']}/restore")
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"]


def test_restore_rolls_back_changes(client):
    """还原：删除项目后还原 → 数据恢复。"""
    pid = _seed(client)
    rec = client.post("/api/v1/backup").json()  # 此刻有 PRJ001

    # 破坏：归档（删除保护，只归档）+ 再建一个多余项目，制造差异
    client.delete(f"/api/v1/projects/{pid}")
    client.post("/api/v1/projects", json={"code": "PRJ999", "name": "多余项目"})

    # 确认此刻 PRJ001 已归档
    con = sqlite3.connect(_db_file())
    is_active = con.execute("SELECT is_active FROM projects WHERE code='PRJ001'").fetchone()[0]
    con.close()
    assert is_active == 0

    # 还原
    r = client.post(f"/api/v1/backup/{rec['id']}/restore", params={"confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["safety_backup"]  # 还原前自动快照已生成

    # 校验：PRJ001 已恢复为 active，PRJ999 被还原掉
    con = sqlite3.connect(_db_file())
    active = con.execute("SELECT code, is_active FROM projects ORDER BY id").fetchall()
    con.close()
    codes = [c for c, a in active]
    assert "PRJ001" in codes
    assert "PRJ999" not in codes
    prj001 = next((a for c, a in active if c == "PRJ001"), None)
    assert prj001 == 1


def test_restore_missing_record(client):
    """还原不存在的记录 → 404。"""
    client.post("/api/v1/backup")  # 确保 backup 目录存在
    assert client.post("/api/v1/backup/9999/restore", params={"confirm": True}).status_code == 404
