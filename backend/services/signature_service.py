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

#: 签名图高度（像素）。一行文字约 20px，签名稍大一点才像手写。
SIGN_HEIGHT_PX = 34

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


def _col_px(ws, col: int) -> int:
    """列宽（字符）→ 像素。Excel 默认字体下约 7px/字符 + 5px 内边距。"""
    dim = ws.column_dimensions.get(get_column_letter(col))
    width = getattr(dim, "width", None) or 8.43
    return int(width * 7 + 5)


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


def _padded_image(path, pad_px: int, height_px: int, pad_top: int = 0) -> str:
    """在签名图左边补透明留白，返回临时文件路径。

    为什么要绕这一圈：合并单元格里放图片时，LibreOffice 只认"锚在哪个格"，
    **会忽略 colOff 水平偏移**（实测导出 PDF 后签名又压回文字上）。
    把留白画进图片里就绕开了这个限制 —— 锚点始终是合并区左上角，
    左边那段透明像素自然让开「编　　制：」几个字。
    """
    import tempfile
    from pathlib import Path as _Path

    from PIL import Image as PILImage

    src = PILImage.open(path).convert("RGBA")
    if src.height != height_px and src.height > 0:
        ratio = height_px / src.height
        src = src.resize((max(1, int(src.width * ratio)), height_px), PILImage.LANCZOS)

    canvas = PILImage.new(
        "RGBA", (pad_px + src.width, pad_top + height_px), (255, 255, 255, 0)
    )
    canvas.paste(src, (pad_px, pad_top), src)

    out = _Path(tempfile.gettempdir()) / f"pitwatch_sig_pad_{pad_px}_{pad_top}_{src.width}.png"
    canvas.save(out, format="PNG", dpi=(96, 96))
    return str(out)


def _place_anchor(ws, row: int, col: int, w_px: int, h_px: int, cell=None):
    """签名该锚在哪。返回 Excel 的 "E17" 字符串，或者一个偏移锚对象。

    **合并单元格是这里最大的坑**：签字栏常写成 C17:D17 这种合并格
    （「编　　制：」后面留出签字的位置）。合并区里只有左上角格有效，
    图片锚在 D17 会被 Excel 画到 **C17** —— 正好盖住「编制：」那几个字。

    所以：
    · 不在合并区 → 右边一格（紧挨着，最紧凑）；
    · 在合并区 → **锚在合并区内部、紧贴文字右端**（用 colOff 偏移）。
      既不用跑到合并区外面（那样「编制：」和签名之间会空出一整列，
      用户 2026-11-13 提的就是这个："签名前不要留太多空白"），
      也不会压到右边的「监测单位：」（那是另一块合并区）。
    """
    # 纵向：签字要**压在横线上**。行高 35pt(47px)、图高 34px 时，
    # 图直接锚在行左上角会浮在半空，横线（下边框）在行底 —— 用户 2026-11-13
    # 报的"不在横线上"就是这个。所以往下留出 pad_top，让图底边贴住行底。
    row_px = _row_px(ws, row)
    pad_top = max(0, row_px - h_px - 2)

    rng = _merged_of(ws, row, col)
    if rng is None:
        return ("cell", col + 1, 0, pad_top)

    # 标签右边一列如果是空白格、**而且带下边框**，那才是母版留的「签字线格」
    # （签字页：C17:D17「编　　制：」+ E17:G17 带下边框的空白格）。
    # 数据表那些「现场监测人：」右边只是普通空白列，没有横线 —— 签名要是也
    # 跑到那一列，和文字之间会隔出 60~70pt 的空白，看着很散（用户 2026-11-13
    # 截图："这是不是很不协调"）。那种情况用下面的"贴文字右端"。
    next_col = rng.max_col + 1
    next_cell = ws.cell(row=row, column=next_col)
    has_rule = getattr(getattr(next_cell.border, "bottom", None), "style", None) is not None
    if next_cell.value in (None, "") and has_rule:
        return ("cell", next_col, 0, pad_top)

    # 右边被占了（如「项目负责人：」右边是「监测单位：」）→ 塞回合并区内部，
    # 紧贴文字右端。**不能用** AnchorMarker + colOff 做水平偏移：
    # LibreOffice 转 PDF 时忽略它，图会回到合并区左上角压住文字。
    total = sum(_col_px(ws, c) for c in range(rng.min_col, rng.max_col + 1))
    start, text_w = _text_metrics(cell, total) if cell is not None else (0.0, 0.0)
    offset = int(start + text_w) + 8
    if offset < 4:
        offset = 0
    return ("pad", rng.min_col, offset, pad_top)


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
                    # 高度不能超过行高：超了 LibreOffice 会把整张图**按比例缩小**去
                    # 适应单元格，连带把左留白也缩掉 —— 签名又跑回文字上，
                    # 而且变小（2026-11-13 实测：34px 的图被压成 10pt）。
                    row_h = _row_px(ws, row_idx)
                    new_h = min(SIGN_HEIGHT_PX, max(12, row_h - 3))
                    new_w = max(12, int(new_h * w / h))
                    if new_w > MAX_SIGN_WIDTH_PX:
                        new_w = MAX_SIGN_WIDTH_PX
                        new_h = max(10, int(new_w * h / w))
                    img.height, img.width = new_h, new_w

                cell = ws.cell(row=row_idx, column=col_idx)
                mode, start_col, pad_left, pad_top = _place_anchor(
                    ws, row_idx, col_idx, new_w, new_h, cell
                )
                # 统一走 _padded_image（哪怕不留白）：它会顺手把 PNG 重存成
                # 96 DPI。用户上传的签名往往没有 DPI 信息，LibreOffice 会把
                # 这种图统一缩到 0.4 倍（实测 17px 渲染成 5pt）。
                padded = _padded_image(path, pad_left, new_h, pad_top)
                pimg = XLImage(padded)
                pimg.height = new_h + pad_top
                pimg.width = new_w + pad_left
                ws.add_image(pimg, f"{get_column_letter(start_col)}{row_idx}")
                stamped += 1
            except Exception:  # noqa: BLE001 —— 某处贴不上不该影响整份日报
                continue

    return stamped
