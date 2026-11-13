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
import re
import zipfile

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


# ───────────── 每个签字栏一张（2026-11-13 用户要求） ─────────────


def _clear_all() -> None:
    signature_service.delete_signature()  # 旧通用图
    for r in signature_service.ROLES:
        signature_service.delete_signature(r["key"])


def test_roles_cover_the_whole_template() -> None:
    """六个角色要覆盖母版里的 11 处签字栏。"""
    names = {r["name"] for r in signature_service.ROLES}
    assert names == {"编制", "审核", "项目负责人", "现场监测人", "计算人", "校核人"}


def test_each_role_stamps_its_own_slot() -> None:
    """编制和审核是两个人 → 各盖各的栏；没配的栏留空，不拿别人的顶。"""
    _clear_all()
    signature_service.save_role_signature("editor", _make_signature_png())
    signature_service.save_role_signature("reviewer", _make_signature_png())

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "编       制："
    ws["A2"] = "审       核："
    ws["A3"] = "现场监测人："

    assert signature_service.stamp_workbook(wb) == 2, "只该盖编制和审核两栏"
    assert len(ws._images) == 2


def test_same_role_in_two_sheets_uses_one_file() -> None:
    """两处数据表的「计算人」是同一个人 → 一张图，两个 sheet 都盖。"""
    _clear_all()
    signature_service.save_role_signature("calculator", _make_signature_png())

    wb = Workbook()
    ws1 = wb.active
    ws1["A1"] = "计算人："
    ws2 = wb.create_sheet("第二张")
    ws2["A1"] = "计算人："

    assert signature_service.stamp_workbook(wb) == 2
    assert len(ws1._images) == 1 and len(ws2._images) == 1


def test_unset_roles_are_left_blank() -> None:
    """一张都没配 → 一个字都不盖（不能因为别人配了就顺手签上）。"""
    _clear_all()
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "现场监测人："
    ws["A2"] = "计算人："
    assert signature_service.stamp_workbook(wb) == 0
    assert not getattr(ws, "_images", [])


def test_legacy_single_signature_still_works() -> None:
    """老装法只有一张 default.png → 所有栏都用它（向后兼容）。"""
    _clear_all()
    signature_service.save_signature(_make_signature_png())

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "现场监测人："
    ws["A2"] = "计算人："
    assert signature_service.stamp_workbook(wb) == 2


def test_role_endpoints(client) -> None:
    _clear_all()
    roles = client.get("/api/v1/settings/signatures").json()["roles"]
    assert len(roles) == 6
    assert all(r["exists"] is False for r in roles)

    r = client.post(
        "/api/v1/settings/signatures/editor",
        files={"file": ("s.png", _make_signature_png(), "image/png")},
    )
    assert r.status_code == 200, r.text

    after = {x["role"]: x for x in client.get("/api/v1/settings/signatures").json()["roles"]}
    assert after["editor"]["exists"] is True
    assert after["editor"]["image"].startswith("data:image/png;base64,")
    assert after["reviewer"]["exists"] is False

    assert client.delete("/api/v1/settings/signatures/editor").status_code == 200
    back = {x["role"]: x for x in client.get("/api/v1/settings/signatures").json()["roles"]}
    assert back["editor"]["exists"] is False


def test_unknown_role_rejected(client) -> None:
    r = client.post(
        "/api/v1/settings/signatures/not-a-role",
        files={"file": ("s.png", _make_signature_png(), "image/png")},
    )
    assert r.status_code == 404
    assert client.delete("/api/v1/settings/signatures/not-a-role").status_code == 404


def test_anchor_lands_on_the_signature_line() -> None:
    """签名要锚在**签字线格**上，并往下贴住横线。

    这份母版的结构是：C17:D17「编　　制：」+ E17:G17 带下边框的空白格。
    ⚠ 2026-11-13 用户三次反馈串起来看：
    ① 锚 D17 → 被画回 C17，压字；
    ② 锚 E17 → 横向上对了，但图浮在行顶，横线在行底 → 用户："不在横线上"。
    所以既要锚到右边那一格，也要用 pad_top 把它压到行底。
    """
    _clear_all()
    signature_service.save_role_signature("editor", _make_signature_png())

    wb = Workbook()
    ws = wb.active
    # 用真实母版的列宽（C=11、D=16 字符）—— 默认 8.43 太窄，塞不下「文字 + 图」
    ws.column_dimensions["C"].width = 11
    ws.column_dimensions["D"].width = 16
    ws.row_dimensions[17].height = 35  # 真实母版的签字行高（35pt）
    ws.merge_cells("C17:D17")
    ws["C17"] = "编       制："

    assert signature_service.stamp_workbook(wb) == 1

    # 坐标要**存盘后从 XML 里读**：add_image 传字符串坐标时，内存里的 anchor
    # 还是个字符串（openpyxl 保存时才转成 twoCellAnchor），直接读会 AttributeError。
    buf = io.BytesIO()
    wb.save(buf)
    xml = zipfile.ZipFile(buf).read("xl/drawings/drawing1.xml").decode("utf-8")
    col = int(re.search(r"<col>(\d+)</col>", xml).group(1))
    row = int(re.search(r"<row>(\d+)</row>", xml).group(1))
    col_off = int(re.search(r"<colOff>(\d+)</colOff>", xml).group(1))
    ext_cy = int(re.search(r"cy=\"(\d+)\"", xml).group(1))
    assert col == 4, f"应锚到 E 列（签字线格 E17:G17），实际 {col}（0 基）"
    assert col_off == 0
    # 行高 35pt(47px)、图高 34px → 顶部补 ~10px 让它压在横线上
    assert ext_cy > 9525 * 34, "图片高度应该大于签名本身（顶部有留白，用来贴横线）"
    assert row == 16


def test_wide_signature_is_width_capped() -> None:
    """长条签名不能横跨三四列压到隔壁栏。"""
    from PIL import Image, ImageDraw

    _clear_all()
    img = Image.new("RGB", (1200, 200), "white")
    d = ImageDraw.Draw(img)
    d.line([(50, 150), (600, 40), (1150, 150)], fill="black", width=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    signature_service.save_role_signature("editor", buf.getvalue())

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "编制："
    signature_service.stamp_workbook(wb)
    assert ws._images[0].width <= signature_service.MAX_SIGN_WIDTH_PX


def test_anchor_inside_merged_when_right_is_occupied() -> None:
    """「项目负责人：」右边紧挨着「监测单位：」—— 不能再往右放，要塞进合并区内部。

    ⚠ 2026-11-13 实测：锚到 G37 后签名压在「监测单位：中铁二十四局…」上。
    """
    _clear_all()
    signature_service.save_role_signature("principal", _make_signature_png())

    wb = Workbook()
    ws = wb.active
    ws.merge_cells("A37:F37")
    ws["A37"] = "项目负责人："
    ws.merge_cells("G37:L37")
    ws["G37"] = "监测单位：中铁二十四局集团上海铁建工程有限公司"

    assert signature_service.stamp_workbook(wb) == 1

    buf = io.BytesIO()
    wb.save(buf)
    xml = zipfile.ZipFile(buf).read("xl/drawings/drawing1.xml").decode("utf-8")
    col = int(re.search(r"<col>(\d+)</col>", xml).group(1))
    assert col == 0, "应锚在 A 列（合并区左上角），而不是跑到 G 列压住监测单位"
