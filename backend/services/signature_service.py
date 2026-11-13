# -*- coding: utf-8 -*-
"""电子签名：把签名图片盖到日报的签字栏。

用户 2026-10-13：「给日报里加个电子签名，每天都签字很烦人」。
用户 2026-11-13：「把这些签字栏都单独拉出来，我来自己放 png 签名」
—— 一天里编制/审核/校核/监测是不同的人，一张图盖满 11 处等于伪造他人签字。

【怎么定位签字位】
不写死坐标 —— 不同母版的签字栏位置完全不同（有的是 A35，有的在单独一页）。
按**标签文字**找：扫每个 sheet，找「现场监测人：」「计算人：」「校核人：」
「项目负责人：」「编 制：」「审 核：」这类单元格，把图锚在它**右边一格**。

【一个角色一张图】
11 处签字栏归成 6 个角色（两处数据表里的同名栏用同一张签名）：
编制 / 审核 / 项目负责人 / 现场监测人 / 计算人 / 校核人。
没配图的角色**留空不盖** —— 空着好过盖错人。
另有 `default.png` 作兼容：老装法只有一张图时，所有栏都用它。

【为什么贴右边而不是覆盖原格】
标签所在格子里有文字，图盖上去会把「现场监测人：」挡住。贴右边既保住文字，
又正好落在原本手写签名的空白处。

【尺寸】
按一行文字的高度缩放（约 34px 高、宽度按原图比例）。原来签名照片动辄上千像素，
不缩放会一张图盖满半张表。
"""
from __future__ import annotations

import io
from pathlib import Path

from openpyxl.utils import get_column_letter

from backend.core.config import settings

#: 签字角色。顺序 = 日报里出现的先后（签字页三个 → 数据表三个）。
#: `aliases` 是同一角色在不同母版里的写法差异。
ROLES: list[dict] = [
    {"key": "editor", "name": "编制", "aliases": []},
    {"key": "reviewer", "name": "审核", "aliases": []},
    {"key": "principal", "name": "项目负责人", "aliases": ["项目负责"]},
    {"key": "observer", "name": "现场监测人", "aliases": ["监测人"]},
    {"key": "calculator", "name": "计算人", "aliases": []},
    {"key": "checker", "name": "校核人", "aliases": []},
]

#: (别名, 角色)，按别名长度降序 —— 「项目负责人」必须先于「项目负责」被匹配。
_MATCH: list[tuple[str, dict]] = sorted(
    ((alias, role) for role in ROLES for alias in [role["name"], *role["aliases"]]),
    key=lambda pair: -len(pair[0]),
)

#: 旧版通用签名：只有一张图时盖所有栏（兼容 2026-11-13 之前的装法）
LEGACY_FILE = "default.png"

#: 数据表里签名的高度（像素）。跟签字页一样大 —— 以前只有 26px，
#: 横向表整页按 49% 打印，落到纸上才 3.4mm（用户：「打印出来很小」）。
#: 行高不够时会把那一行撑开，别让它溢到下面跟另一栏挤在一起。
SIGN_TABLE_HEIGHT_PX = 46

#: 签字页（带签字横线的大行）的签名高度。行高 35pt(46px) 时几乎撑满 ——
#: 原来 34px 只占三分之二，用户 2026-11-13：「签字页上的签名搞大一点」。
SIGN_PAGE_HEIGHT_PX = 46

#: 签名高度上限。行再高也别让签名长到压住上下两行。
SIGN_MAX_HEIGHT_PX = 64

#: 位图超采样倍数。图片**显示**多大由 `img.width/height` 决定，
#: 像素数只影响清晰度 —— 按 1 倍塞进去，打印时是在放大一张几十像素的小图，
#: 用户 2026-11-13 报的「都马赛克了」就是这个。
SIGN_SUPERSAMPLE = 4

#: 最大宽度。签名照片常是 4:1 的长条，只按高度缩放会横跨三四列，
#: 压到隔壁栏（如「计算人」）上面去。超过这个宽度就按宽度等比缩。
MAX_SIGN_WIDTH_PX = 150


class SignatureError(RuntimeError):
    """签名文件本身有问题（格式不对、读不出来）。"""


def get_role(key: str) -> dict | None:
    return next((r for r in ROLES if r["key"] == key), None)


def _role_dir() -> Path:
    return settings.signature_dir


def _path_for(role: dict) -> Path | None:
    """角色的签名文件。文件名优先中文名（用户手动放文件时最直观），其次英文 key。"""
    d = _role_dir()
    for name in (f"{role['name']}.png", f"{role['key']}.png"):
        p = d / name
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def _legacy_path() -> Path | None:
    p = _role_dir() / LEGACY_FILE
    return p if p.is_file() and p.stat().st_size > 0 else None


def role_of(text: str) -> dict | None:
    """这段文字是哪一栏的签字标签。不是签字栏返回 None。"""
    t = (text or "").strip().replace(" ", "").replace("\u3000", "").replace("\n", "")
    # 必须以冒号结尾 —— 正文里的「编制说明」「项目负责人审核后发出」不算
    if not t.endswith(("：", ":")):
        return None
    core = t.rstrip("：:").strip()
    for alias, role in _MATCH:
        if core == alias or core.endswith(alias):
            return role
    return None


def _is_sign_label(text: str) -> bool:
    """兼容旧调用：是不是签字栏标签。"""
    return role_of(text) is not None


def has_signature() -> bool:
    """有没有任何一张签名（按角色的或旧的通用签名）。"""
    return any(_path_for(r) for r in ROLES) or _legacy_path() is not None


def save_signature(data: bytes, *, role: str | None = None) -> None:
    """保存签名图（统一转 PNG，裁掉四周大片空白）。

    `role=None` 时存成通用签名 `default.png`（老行为）；否则存该角色专属的一张。

    为什么要裁：用户多半是手机拍一张纸，四周全是白边 —— 不裁的话贴到日报上
    会变成一小块字配一大片白，把表格撑变形。
    """
    from PIL import Image, ImageChops

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise SignatureError("这个文件不是有效的图片（请上传 PNG/JPG）") from exc

    img = img.convert("RGBA")

    # 裁掉四周空白。分两种情况：
    # · 透明底 PNG（电子签名常见）→ 用 alpha 通道求边界
    # · 白底照片/扫描件 → 跟纯白做差求边界
    # ⚠ 不能直接拿 RGBA 跟"透明白"做差：整张图的 alpha 都是 255，会被判成"处处不同"，
    #   结果一点也裁不掉（第一版就是这么写的，测试当场抓出来）。
    bbox = None
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 255:
        bbox = alpha.getbbox()
    else:
        rgb = img.convert("RGB")
        white = Image.new("RGB", rgb.size, (255, 255, 255))
        bbox = ImageChops.difference(rgb, white).getbbox()
    if bbox:
        img = img.crop(bbox)

    # 太大的缩到 600px 宽（够清晰又不占地方）
    if img.width > 600:
        ratio = 600 / img.width
        img = img.resize((600, max(1, int(img.height * ratio))), Image.LANCZOS)

    target = get_role(role) if role else None
    if role and target is None:
        raise SignatureError(f"未知的签字角色：{role}")

    _role_dir().mkdir(parents=True, exist_ok=True)
    name = f"{target['name']}.png" if target else LEGACY_FILE
    # 显式写 DPI：不写的话 LibreOffice 转 PDF 时会按默认比例把图缩到 0.4 倍
    img.save(_role_dir() / name, format="PNG", dpi=(96, 96))


def save_role_signature(role_key: str, data: bytes) -> None:
    save_signature(data, role=role_key)


def delete_signature(role: str | None = None) -> None:
    """删掉某个角色的签名（`role=None` 删旧的通用签名）。"""
    if role is None:
        for p in (_role_dir() / LEGACY_FILE,):
            if p.exists():
                p.unlink()
        return

    target = get_role(role)
    if target is None:
        return
    for name in (f"{target['name']}.png", f"{target['key']}.png"):
        p = _role_dir() / name
        if p.exists():
            p.unlink()


def list_roles() -> list[dict]:
    """给界面用：每个角色有没有配签名、长什么样。"""
    import base64

    out = []
    for role in ROLES:
        p = _path_for(role)
        out.append(
            {
                "role": role["key"],
                "name": role["name"],
                "exists": p is not None,
                "image": (
                    "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
                    if p
                    else None
                ),
            }
        )
    return out


def _merged_of(ws, row: int, col: int):
    """这个格子在哪个合并区里（不在任何合并区返回 None）。"""
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= row <= rng.max_row and rng.min_col <= col <= rng.max_col:
            return rng
    return None


def _row_px(ws, row: int) -> int:
    """行高（磅）→ 像素。没设过就是默认 14.25 磅。"""
    dim = ws.row_dimensions.get(row)
    height = getattr(dim, "height", None) or 14.25
    return int(float(height) * 96 / 72)


def _mdw(ws) -> float:
    """这个工作簿「1 个字符宽」等于多少像素（Excel 列宽单位 = 默认字体数字 0 的宽度）。

    ⚠ 不能一律按 7 算（那是 Calibri 11）。中文母版的默认字体是**宋体 11**，
    中文版 Excel 里是 8px，而服务器 LibreOffice 上宋体被 Noto CJK 顶替、
    实际画出来约 9.15px。按 7 估会把签字线格算窄三成，
    居中就明显偏左（2026-11-13 实测偏了线长的 11%）。

    取 8.5 折中：Excel 与服务器 PDF 两边的偏差都在 4% 以内。
    """
    name = ""
    try:
        fonts = list(getattr(ws.parent, "_fonts", []) or [])
        name = (getattr(fonts[0], "name", "") or "") if fonts else ""
    except Exception:  # noqa: BLE001 —— 读不到就按拉丁字体算
        name = ""
    return 8.5 if any(k in name for k in ("宋", "黑体", "雅黑", "SimSun", "SimHei")) else 7.0


def _col_px(ws, col: int) -> int:
    """列宽（字符）→ 像素。没设过就用工作表默认列宽（中文母版常是 9，不是 8.43）。"""
    dim = ws.column_dimensions.get(get_column_letter(col))
    width = getattr(dim, "width", None)
    if not width:
        width = getattr(ws.sheet_format, "defaultColWidth", None) or 8.43
    return int(width * _mdw(ws) + 5)


def _text_metrics(cell, total_px: float) -> tuple[float, float]:
    """算出标签文字在这块合并区里**从哪开始、有多宽**（像素）。

    两个细节不能漏，漏了就压字：
    1. **字号**：母版里签字栏常是 16pt（不是默认 11pt）→ 每个汉字约 21px，
       按 11pt 估会少算 40%，签名正好落在「制：」上（2026-11-13 实测）。
    2. **对齐方式**：这块是 `center` —— 文字浮在合并区中间，左边还空着一段。
       按"左对齐"估同样会压字。

    字符宽度按"汉字 = 字号×96/72"，半角字符按一半算。
    """
    text = cell.value if isinstance(cell.value, str) else ""
    size_pt = getattr(cell.font, "sz", None) or 11
    unit = float(size_pt) * 96 / 72  # 磅 → 像素

    width = 0.0
    for ch in text.strip():
        if ch == " ":
            width += unit * 0.5
        elif ord(ch) > 127:
            width += unit
        else:
            width += unit * 0.5

    align = (getattr(cell.alignment, "horizontal", None) or "general").lower()
    if align == "center":
        start = max(0.0, (total_px - width) / 2)
    elif align == "right":
        start = max(0.0, total_px - width)
    else:
        start = 0.0

    return start, width


def _paper_to_alpha(rgb):
    """把"白纸 + 墨"的图转成"墨 + 透明底"。

    **为什么必须这么做**：签名要压在单元格的签字横线上。图片要是带不透明白底，
    横线在图片矩形范围内会被整块白盖掉 —— 用户 2026-11-13 截图里的那句
    「这个横线被吃了」就是它（横线从签名右端才重新露出来）。

    做法：亮度当透明度（纸白 255 → alpha 0），颜色按"白底反预乘"还原成真实墨色，
    这样透明以后压在什么背景上，看起来都和原来一样，只是不再挡住底下的东西。
    """
    from PIL import Image as PILImage

    rgb_s = rgb.tobytes()
    lum_s = rgb.convert("L").tobytes()

    out = bytearray(len(lum_s) * 4)
    for i, lum in enumerate(lum_s):
        a = 255 - lum
        o = i * 4
        if a <= 0:  # 白纸 —— 全透明
            out[o : o + 4] = b"\xff\xff\xff\x00"
            continue
        if a >= 255:  # 纯墨
            out[o] = rgb_s[i * 3]
            out[o + 1] = rgb_s[i * 3 + 1]
            out[o + 2] = rgb_s[i * 3 + 2]
            out[o + 3] = 255
            continue
        # 反预乘：C = a*F + (1-a)*255  →  F = (C - 255 + a) * 255 / a
        k = 255.0 / a
        out[o] = min(255, max(0, int((rgb_s[i * 3] - 255 + a) * k)))
        out[o + 1] = min(255, max(0, int((rgb_s[i * 3 + 1] - 255 + a) * k)))
        out[o + 2] = min(255, max(0, int((rgb_s[i * 3 + 2] - 255 + a) * k)))
        out[o + 3] = a

    return PILImage.frombytes("RGBA", rgb.size, bytes(out))


def _padded_image(
    path, pad_px: int, height_px: int, pad_top: int = 0, supersample: int = 1
) -> str:
    """把签名图转成"墨 + 透明底"、左边补透明留白，返回临时文件路径。

    三件事：

    1. **左边补透明留白**。合并单元格里放图片时，LibreOffice 只认"锚在哪个格"，
       **会忽略 colOff 水平偏移**（实测导出 PDF 后签名又压回文字上）。
       把留白画进图片里就绕开了这个限制 —— 锚点始终是合并区左上角，
       左边那段透明像素自然让开「编　　制：」几个字。

    2. **白底转透明**（`_paper_to_alpha`）。签名往往比行高、比签字线还高，
       再带一块不透明白底，就会把单元格下边框整段盖掉 —— 用户看到的
       「横线被吃了」。透明以后，横线从签名笔画之间照常穿过去，和真手写一样。

    3. **超采样**（`supersample`）。位图按**显示尺寸的 N 倍**渲染，显示尺寸另由
       `img.width/height` 指定 —— 图片的像素数不影响它在 Excel 里多大，只影响
       清晰度。不这么做，26px 高的图印在纸上被放大 4 倍，就是一格一格的马赛克
       （用户 2026-11-13：「打印出来很小啊 都马赛克了」）。
    """
    import hashlib
    import tempfile
    from pathlib import Path as _Path

    from PIL import Image as PILImage

    ss = max(1, int(supersample))
    src = PILImage.open(path).convert("RGBA")

    # 透明底 PNG 先铺白，统一按"白纸 + 墨"处理。
    # ⚠ 不能直接 `convert("RGB")`：透明区会变成**黑色** —— 黑底黑字整块糊死
    #   （2026-11-13 实测 3 处变黑块）。
    if src.getchannel("A").getextrema()[0] < 255:
        white = PILImage.new("RGBA", src.size, (255, 255, 255, 255))
        src = PILImage.alpha_composite(white, src)
    rgb = src.convert("RGB")

    target_h = max(1, height_px * ss)
    if rgb.height != target_h and rgb.height > 0:
        ratio = target_h / rgb.height
        rgb = rgb.resize((max(1, int(rgb.width * ratio)), target_h), PILImage.LANCZOS)

    # 提高对比：亮于 200 的一律压成纯白、暗于 110 的压成纯黑。
    # 签名缩到二十几像素高时，抗锯齿会把笔画糊成一片浅灰（用户："看不清"）。
    _lut = [255 if v > 200 else (0 if v < 110 else int((v - 110) * 255 / 90)) for v in range(256)]
    rgb = rgb.point(_lut * 3)

    ink = _paper_to_alpha(rgb)

    # 留白也要按同样的倍数放大 —— 位图的尺寸是"显示尺寸 × 倍数"
    canvas = PILImage.new(
        "RGBA",
        (pad_px * ss + ink.width, pad_top * ss + ink.height),
        (0, 0, 0, 0),
    )
    canvas.alpha_composite(ink, (pad_px * ss, pad_top * ss))

    # 文件名带内容指纹：不同角色的签名若宽高、留白恰好一样，会写到同一个
    # 临时文件上 —— openpyxl 是**保存时才读文件**，后写的那张会把前面那几栏
    # 全盖成同一个人（签字人张冠李戴，比贴不上严重得多）。
    digest = hashlib.sha1(
        b"%s|%d|%d|%d|%d|" % (str(path).encode(), pad_px, height_px, pad_top, ss)
        + _Path(path).read_bytes()
    ).hexdigest()[:16]
    out = _Path(tempfile.gettempdir()) / f"pitwatch_sig_{digest}.png"
    # DPI 跟着倍数走：位图是显示尺寸的 ss 倍，标 96*ss 才能让阅读器按原大小显示
    canvas.save(out, format="PNG", dpi=(96 * ss, 96 * ss))
    return str(out)


def _line_cell(ws, row: int, col: int):
    """这一栏的「签字线格」—— 标签右边一块**空白且带下边框**的格子；没有返回 None。

    母版把签字线做成**另一块合并格**（签字页：C17:D17「编　　制：」+
    E17:G17 带下边框的空白格）。数据表那些「现场监测人：」右边只是普通空白列、
    没有横线，不能当成签字线格（签名跑到那一列会和文字隔开 60~70pt，很散）。

    ⚠ **只认「标签本身在合并区里」的那种排版**：数据表是逐格画边框的，
    单格标签右边那格往往也带下边框（表格网格），按边框判会把签名甩到隔壁列去。
    """
    rng = _merged_of(ws, row, col)
    if rng is None:
        return None
    next_col = rng.max_col + 1
    cell = ws.cell(row=row, column=next_col)
    has_rule = getattr(getattr(cell.border, "bottom", None), "style", None) is not None
    if cell.value in (None, "") and has_rule:
        return cell
    return None


def _span_px(ws, row: int, col: int) -> int:
    """这一格（连同它所在的合并区）有多宽（像素）。"""
    rng = _merged_of(ws, row, col)
    cols = range(rng.min_col, rng.max_col + 1) if rng is not None else [col]
    return sum(_col_px(ws, c) for c in cols)


def _bottom_on_line(ws, row: int, col: int, h_px: int) -> tuple[int, int]:
    """让图片**底边压住本行的下边框**（签字横线），返回 (锚点行, pad_top)。

    图比这一行还高时**往上借行**，多出来的高度长在上面 —— 不能靠"pad_top 取 0"
    凑合，那样图会整体下坠，跑到横线下面去（用户 2026-11-13 第二次：
    「签字页上的签名搞大一点」，直接把图调高就会踩这个坑）。

    借到的那一行如果是某个合并区的一部分，Excel 会把图画在合并区左上角，
    所以锚点要跟着上移到合并区首行，并把跨过的行高补进 pad_top。
    """
    pad = _row_px(ws, row) - h_px - 2
    anchor_row = row
    while pad < 0 and anchor_row > 1:
        anchor_row -= 1
        pad += _row_px(ws, anchor_row)

    rng = _merged_of(ws, anchor_row, col)
    while rng is not None and rng.min_row < anchor_row:
        for r in range(rng.min_row, anchor_row):
            pad += _row_px(ws, r)
        anchor_row = rng.min_row
        rng = _merged_of(ws, anchor_row, col)

    return anchor_row, max(0, pad)


def _place_anchor(ws, row: int, col: int, w_px: int, h_px: int, cell=None):
    """签名该锚在哪。返回 (模式, 列, 行, pad_left, pad_top)。

    **合并单元格是这里最大的坑**：签字栏常写成 C17:D17 这种合并格
    （「编　　制：」后面留出签字的位置）。合并区里只有左上角格有效，
    图片锚在 D17 会被 Excel 画到 **C17** —— 正好盖住「编制：」那几个字。

    所以：
    · 有签字线格（右边那块带下边框的空白格）→ 锚进去，**水平居中压在线上**；
    · 不在合并区 → 右边一格（紧挨着，最紧凑）；
    · 右边被占了（如「项目负责人：」右边是「监测单位：」）→ 塞回合并区内部、
      紧贴文字右端（不用 colOff：LibreOffice 转 PDF 时会忽略它，图回到合并区
      左上角压住文字）。
    """
    rng = _merged_of(ws, row, col)

    # ① 有签字线格：横向居中，纵向压线（图高超过行高就往上借行）
    line = _line_cell(ws, row, col)
    if line is not None:
        span = _span_px(ws, row, line.column)
        pad_left = max(0, (span - w_px) // 2)
        anchor_row, pad_top = _bottom_on_line(ws, row, line.column, h_px)
        return ("cell", line.column, anchor_row, pad_left, pad_top)

    # ② 普通格子：右边一格，左对齐（那里没有横线，居中反而像乱放）
    if rng is None:
        return ("cell", col + 1, row, 0, max(0, _row_px(ws, row) - h_px - 2))

    # ③ 右边被占了 → 塞回合并区内部，紧贴文字右端。
    # **不能用** AnchorMarker + colOff 做水平偏移：LibreOffice 转 PDF 时忽略它。
    total = sum(_col_px(ws, c) for c in range(rng.min_col, rng.max_col + 1))
    start, text_w = _text_metrics(cell, total) if cell is not None else (0.0, 0.0)
    offset = int(start + text_w) + 8
    if offset < 4:
        offset = 0
    return ("pad", rng.min_col, row, offset, max(0, _row_px(ws, row) - h_px - 2))


def stamp_workbook(wb) -> int:
    """把各角色的签名盖到对应签字栏。返回贴了几处（一张签名都没有返回 0）。

    查找顺序：**该角色自己的图** → 旧的通用图 `default.png` → 都不盖。
    """
    from openpyxl.drawing.image import Image as XLImage

    legacy = _legacy_path()
    if not has_signature():
        return 0

    stamped = 0

    for ws in wb.worksheets:
        targets: list[tuple[int, int, dict]] = []
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    role = role_of(cell.value)
                    if role:
                        targets.append((cell.row, cell.column, role))

        for row_idx, col_idx, role in targets:
            path = _path_for(role) or legacy
            if path is None:
                continue  # 这一栏没配签名 —— 留空，不拿别人的图顶

            # 贴到标签右边一格。
            # ⚠ 不要用 `col_idx >= ws.max_column` 跳过 —— 签字栏常常就在最后一列
            #   （如 J8「校核人：」），跳过会把最右边那处漏掉（测试抓到 3/4）。
            try:
                img = XLImage(str(path))
                w, h = img.width, img.height
                new_w, new_h = w, h
                if w > 0 and h > 0:
                    row_h = _row_px(ws, row_idx)
                    if _line_cell(ws, row_idx, col_idx) is not None:
                        # 签字页那种带横线的大行（35pt≈46px）：签名要**明显大一号**，
                        # 撑到接近行高才像真手写（用户 2026-11-13：
                        # 「签字页上的签名搞大一点」）。矮行靠 _bottom_on_line 往上借行。
                        new_h = min(SIGN_MAX_HEIGHT_PX, max(SIGN_PAGE_HEIGHT_PX, int(row_h * 0.85)))
                    else:
                        # 数据表：行高只有 18~19px，塞进去就是一个指甲盖大的小图，
                        # 印出来只有 3mm 高、还糊（用户 2026-11-13：
                        # 「其他的字也高大一点 打印出来很小啊 都马赛克了」）。
                        # 直接放大又会溢到下面一行、跟下一个签字栏挤在一起
                        # —— 所以**把这一行撑到装得下**，签名才做得大。
                        new_h = SIGN_TABLE_HEIGHT_PX
                        need_px = new_h + 4
                        if need_px > row_h:
                            ws.row_dimensions[row_idx].height = round(need_px * 0.75, 2)
                    new_w = max(12, int(new_h * w / h))
                    if new_w > MAX_SIGN_WIDTH_PX:
                        new_w = MAX_SIGN_WIDTH_PX
                        new_h = max(10, int(new_w * h / w))
                    img.height, img.width = new_h, new_w

                cell = ws.cell(row=row_idx, column=col_idx)
                mode, start_col, start_row, pad_left, pad_top = _place_anchor(
                    ws, row_idx, col_idx, new_w, new_h, cell
                )
                # 统一走 _padded_image（哪怕不留白）：它会顺手把 PNG 重存成
                # 96 DPI。用户上传的签名往往没有 DPI 信息，LibreOffice 会把
                # 这种图统一缩到 0.4 倍（实测 17px 渲染成 5pt）。
                # 位图按显示尺寸的 4 倍渲染：页面上看着一样大，印出来不糊
                # （数据表整页按 49% 打印，26px 的图放大到纸面就是一格一格）。
                padded = _padded_image(
                    path, pad_left, new_h, pad_top, supersample=SIGN_SUPERSAMPLE
                )
                pimg = XLImage(padded)
                pimg.height = new_h + pad_top
                pimg.width = new_w + pad_left
                ws.add_image(pimg, f"{get_column_letter(start_col)}{start_row}")
                stamped += 1
            except Exception:  # noqa: BLE001 —— 某处贴不上不该影响整份日报
                continue

    return stamped
