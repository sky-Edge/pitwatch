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
from openpyxl.styles import Border, Side

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
    # 母版里签字线是**另一块带下边框的合并格**（E17:G17），没有它就不算签字线
    ws.merge_cells("E17:G17")
    ws["E17"].border = Border(bottom=Side(style="thin"))

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
    # 图要比签名本身高（顶部有留白，用来贴横线）
    assert ext_cy > 9525 * 34
    assert row == 16


def _sign_page_book() -> Workbook:
    """照着真母版搭一页：C17:D17「编　　制：」+ E17:G17 带下边框的签字线格。"""
    wb = Workbook()
    ws = wb.active
    for col, width in (("C", 11), ("D", 16), ("E", 20.83)):
        ws.column_dimensions[col].width = width
    ws.row_dimensions[17].height = 35
    ws.merge_cells("C17:D17")
    ws["C17"] = "编       制："
    ws.merge_cells("E17:G17")
    ws["E17"].border = Border(bottom=Side(style="thin"))
    return wb


def _embedded(wb) -> tuple[list, str]:
    """存盘后读回内嵌图片与 drawing1.xml。"""
    from PIL import Image as PILImage

    buf = io.BytesIO()
    wb.save(buf)
    z = zipfile.ZipFile(buf)
    imgs = [
        PILImage.open(io.BytesIO(z.read(n))).convert("RGBA")
        for n in z.namelist()
        if n.startswith("xl/media/")
    ]
    return imgs, z.read("xl/drawings/drawing1.xml").decode("utf-8")


def test_sign_page_signature_is_bigger_and_centered_on_line() -> None:
    """签字页的签名要**大一号**，并且**在签字横线上居中**。

    ⚠ 2026-11-13 用户：「签字页上的签名搞大一点，然后电子签名在中横线居中摆放」。
    原来 34px 只占 35pt 行高的三分之二，而且贴着线格左边放（右边一大截空线）。
    """
    from PIL import Image as PILImage, ImageDraw

    _clear_all()
    # 2.3:1 的签名（用户那几张就这个比例），不会被宽度上限截短
    img = PILImage.new("RGB", (460, 200), "white")
    ImageDraw.Draw(img).line([(60, 165), (150, 40), (280, 160), (360, 60)], fill="black", width=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    signature_service.save_role_signature("editor", buf.getvalue())

    wb = _sign_page_book()
    assert signature_service.stamp_workbook(wb) == 1

    imgs, _ = _embedded(wb)
    assert len(imgs) == 1
    im = imgs[0]
    bbox = im.getchannel("A").getbbox()
    ss = signature_service.SIGN_SUPERSAMPLE
    ink_w, ink_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # 位图是显示尺寸的 ss 倍（印出来才不糊），所以像素高度是 46 × ss
    assert ink_h == signature_service.SIGN_PAGE_HEIGHT_PX * ss, f"签名该撑到 46px，实际 {ink_h}px"
    assert ink_h > signature_service.SIGN_PAGE_HEIGHT_PX, "必须超采样，否则打印是马赛克"

    # 居中：左边补的留白 = (线格宽 - 图宽) / 2（显示尺寸，再乘 ss）
    span = signature_service._span_px(wb.active, 17, 5)
    shown_w = ink_w // ss
    assert bbox[0] == ((span - shown_w) // 2) * ss, "签名没有居中在签字横线上"


def test_table_signature_is_big_and_row_grows_to_fit() -> None:
    """数据表的签名也要大，行高不够就把**那一行**撑开。

    ⚠ 2026-11-13 用户：「其他的字也高大一点，打印出来很小啊，都马赛克了」——
    数据表行高 18px，签名原来只有 26px，横向表按 49% 打印，纸上才 3.4mm。
    直接放大又会溢到下一行、跟下面的签字栏挤成一团，所以要把行撑高。
    """
    from PIL import Image as PILImage, ImageDraw

    _clear_all()
    img = PILImage.new("RGB", (460, 200), "white")
    ImageDraw.Draw(img).line([(60, 165), (150, 40), (280, 160), (360, 60)], fill="black", width=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    signature_service.save_role_signature("observer", buf.getvalue())

    wb = Workbook()
    ws = wb.active
    for col, width in (("A", 14), ("B", 20)):
        ws.column_dimensions[col].width = width
    ws.row_dimensions[35].height = 13.5  # 数据表的默认行高
    ws.merge_cells("A35:B35")
    ws["A35"] = "现场监测人："

    assert signature_service.stamp_workbook(wb) == 1

    # 行被撑到能装下签名（46 + 4 的余量，磅 = px × 0.75）
    grown = ws.row_dimensions[35].height
    assert grown >= signature_service.SIGN_TABLE_HEIGHT_PX * 0.75, f"行没撑高：{grown}pt"

    imgs, _ = _embedded(wb)
    bbox = imgs[0].getchannel("A").getbbox()
    ink_h = bbox[3] - bbox[1]
    assert ink_h == signature_service.SIGN_TABLE_HEIGHT_PX * signature_service.SIGN_SUPERSAMPLE, (
        f"数据表签名该 46px（位图 ×{signature_service.SIGN_SUPERSAMPLE}），实际 {ink_h}px"
    )


def test_cjk_workbook_does_not_use_calibri_char_width() -> None:
    """中文母版（默认字体宋体）的列宽不能按 Calibri 的 7px/字符 算。

    ⚠ 2026-11-13 实测：按 7 算，签字线格 E:G 估窄 28%（282 vs 实际 365），
    居中就偏到线长的 38% 处，肉眼明显偏左。
    """
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.column_dimensions["E"].width = 20.83

    assert signature_service._mdw(ws) == 7.0, "默认 Calibri 该按 7px/字符"
    latin_span = signature_service._span_px(ws, 1, 5)

    wb._fonts[0] = Font(name="宋体", sz=11)  # 中文母版的默认字体
    assert signature_service._mdw(ws) > 7.0, "宋体不能按 Calibri 的 7px/字符 算"
    assert signature_service._span_px(ws, 1, 5) > latin_span


def test_col_px_falls_back_to_sheet_default_width() -> None:
    """没单独设宽度的列要用工作表默认列宽 —— 中文母版常是 9，不是 8.43。"""
    wb = Workbook()
    ws = wb.active
    ws.sheet_format.defaultColWidth = 9.0
    assert signature_service._col_px(ws, 7) == int(9.0 * signature_service._mdw(ws) + 5)


def test_signature_borrows_row_above_when_row_too_short() -> None:
    """行高不够时把图**往上长**，底边仍然压住横线。
    ⚠ 母版里「项目 负责：」那行只有 20.25pt(27px)，签名撑到 46px 的话，
    按老写法 pad_top 只能取 0 → 图整体下坠 19px，掉到横线下面去。
    """
    from PIL import Image as PILImage, ImageDraw

    _clear_all()
    img = PILImage.new("RGB", (460, 200), "white")
    ImageDraw.Draw(img).line([(60, 165), (150, 40), (280, 160), (360, 60)], fill="black", width=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    signature_service.save_role_signature("editor", buf.getvalue())

    wb = Workbook()
    ws = wb.active
    ws.column_dimensions["C"].width = 11
    ws.column_dimensions["D"].width = 16
    ws.row_dimensions[25].height = 20.25  # 真母版的行高
    ws.merge_cells("C25:D25")
    ws["C25"] = "编       制："
    ws.merge_cells("E25:G25")
    ws["E25"].border = Border(bottom=Side(style="thin"))

    assert signature_service.stamp_workbook(wb) == 1

    imgs, xml = _embedded(wb)
    row0 = int(re.search(r"<row>(\d+)</row>", xml).group(1))
    cy = int(re.search(r"cy=\"(\d+)\"", xml).group(1)) // 9525
    assert row0 < 24, f"图比行高还高，锚点该往上借行，实际还在第 {row0 + 1} 行"

    # 借了几行就得够用：图顶到本行底（横线）之间不能是负的
    top_row = row0 + 1
    cover = sum(
        int(float(getattr(ws.row_dimensions.get(r), "height", None) or 14.25) * 96 / 72)
        for r in range(top_row, 26)
    )
    assert cy <= cover, "往上借的行不够高，签名还是会掉到横线下面"


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


def test_transparent_signature_does_not_turn_black() -> None:
    """透明底签名不能糊成黑块。

    ⚠ 2026-11-13：用户上传的签名里有透明底 PNG（笔画黑、背景透明）。
    RGBA→RGB 会把透明区变成黑，再经过对比增强，整个签名位就是一块纯黑。
    """
    from PIL import Image as PILImage, ImageDraw

    _clear_all()
    img = PILImage.new("RGBA", (400, 150), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.line([(30, 110), (150, 30), (280, 100), (370, 40)], fill=(0, 0, 0, 255), width=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    signature_service.save_role_signature("editor", buf.getvalue())

    path = signature_service._path_for(signature_service.get_role("editor"))
    out = signature_service._padded_image(path, 20, 26, 0)

    with PILImage.open(out).convert("RGBA") as im:
        # 判据看 alpha：透明区在 Excel 里就是白纸，不能拿丢掉 alpha 的 RGB 判
        # （透明像素的 RGB 是黑的，那样永远"整块变黑"）
        pixels = list(im.getdata())
        dark = sum(1 for r, g, b, a in pixels if a > 200 and r < 40)
    assert dark / len(pixels) < 0.5, "整块变黑了 —— 透明区没按白纸处理"


def test_signature_does_not_cover_underline(tmp_path) -> None:
    """签名不准把单元格的下边框（签字横线）盖掉。

    ⚠ 2026-11-13 用户截图：「这个横线被吃了」——
    签名图带不透明白底，图片矩形范围内的横线整段消失，只在签名右端才重新出现。
    所以图片必须**墨 + 透明底**：纸白部分 alpha = 0，横线从笔画之间穿过去。
    """
    from PIL import Image as PILImage, ImageDraw

    # 直接给一张"白纸上一条笔画"的图（四周留白不会被裁，正好验证白纸→透明）
    img = PILImage.new("RGB", (300, 100), "white")
    ImageDraw.Draw(img).line([(80, 70), (150, 30), (230, 60)], fill="black", width=6)
    src = tmp_path / "sig.png"
    img.save(src, format="PNG")

    out = signature_service._padded_image(src, 30, 26, 0)

    with PILImage.open(out).convert("RGBA") as im:
        w, h = im.size
        alpha = im.getchannel("A")
        for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            assert alpha.getpixel(xy) == 0, f"{xy} 还是不透明 —— 会盖住横线"
        assert alpha.getpixel((5, h // 2)) == 0, "左边留白必须透明"
        assert max(alpha.getdata()) == 255, "笔画得是实的，不然签名淡到看不见"
        # 笔画中间那块白纸也要透（横线从字缝里穿过去，才像真手写）
        clear = sum(1 for v in alpha.getdata() if v == 0)
        assert clear / (w * h) > 0.3
