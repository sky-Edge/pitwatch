# -*- coding: utf-8 -*-
"""电子签名：导出日报时自动盖到签字栏。

用户 2026-10-13：「给日报里加个电子签名，每天都签字很烦人」。

要钉住四件事：
  ① 没传签名 → 导出照常，只是不签（不能因为没签名就报错）；
  ② 传了签名 → 找得到签字栏，盖上去；
  ③ 签字栏靠**标签文字**定位（换母版也能找），不是写死坐标；
  ④ 存的是会写盘的文件 → 测试目录必须隔离（conftest 已加 MONITOR_SIGNATURE_DIR）。
"""
from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from backend.services import signature_service


def _make_signature_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (240, 80), "white")
    d = ImageDraw.Draw(img)
    d.line([(20, 60), (70, 20), (120, 55), (200, 25)], fill="black", width=5)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ─────────────────────── 上传 / 查询 / 删除 ───────────────────────


def test_signature_lifecycle(client) -> None:
    assert client.get("/api/v1/settings/signature").json()["exists"] is False

    r = client.post(
        "/api/v1/settings/signature",
        files={"file": ("sign.png", _make_signature_png(), "image/png")},
    )
    assert r.status_code == 200, r.text

    body = client.get("/api/v1/settings/signature").json()
    assert body["exists"] is True
    assert body["image"].startswith("data:image/png;base64,")

    assert client.delete("/api/v1/settings/signature").status_code == 200
    assert client.get("/api/v1/settings/signature").json()["exists"] is False


def test_reject_non_image(client) -> None:
    r = client.post(
        "/api/v1/settings/signature",
        files={"file": ("a.txt", b"not an image at all", "text/plain")},
    )
    assert r.status_code == 422
    assert "图片" in r.json()["detail"]


def test_reject_empty_file(client) -> None:
    r = client.post("/api/v1/settings/signature", files={"file": ("e.png", b"", "image/png")})
    assert r.status_code == 422


# ─────────────────────── 往工作簿上盖 ───────────────────────


def test_no_signature_means_no_stamp() -> None:
    signature_service.delete_signature()
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "现场监测人："
    assert signature_service.stamp_workbook(wb) == 0
    assert not getattr(ws, "_images", [])


def test_stamp_finds_labels_by_text() -> None:
    """按标签文字找位置 —— 换一份母版（行号不同）也能找到。"""
    signature_service.save_signature(_make_signature_png())

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "标题"
    ws["C8"] = "现场监测人："
    ws["F8"] = "计算人："
    ws["J8"] = "校核人："
    ws["A20"] = "项目负责人："
    ws["B3"] = "编制说明：这不是签字栏"  # 干扰项，不该被当成签字位

    stamped = signature_service.stamp_workbook(wb)
    assert stamped == 4, f"应该找到 4 处签字栏，实际 {stamped}"
    assert len(ws._images) == 4


def test_stamp_ignores_plain_text() -> None:
    """正文里出现「负责人」但没冒号 → 不是签字栏。"""
    signature_service.save_signature(_make_signature_png())
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "本报告由项目负责人审核后发出"
    assert signature_service.stamp_workbook(wb) == 0


def test_signature_is_trimmed_and_converted() -> None:
    """上传的图会裁掉白边、转成 PNG —— 贴到表上才不会撑出一大片空白。"""
    signature_service.save_signature(_make_signature_png())
    path = signature_service.settings.signature_dir / "default.png"
    assert path.is_file()

    from PIL import Image

    with Image.open(path) as im:
        assert im.format == "PNG"
        # 原图 240x80 四周有白边，裁完应当明显小一圈
        assert im.width <= 240 and im.height <= 80
        assert im.width < 240 or im.height < 80
