# -*- coding: utf-8 -*-
"""电子签名：把签名图片盖到日报的签字栏。

用户 2026-10-13：「给日报里加个电子签名，每天都签字很烦人」。

【怎么定位签字位】
不写死坐标 —— 不同母版的签字栏位置完全不同（有的是 A35，有的在单独一页）。
这里按**标签文字**找：扫每个 sheet，找「现场监测人：」「计算人：」「校核人：」
「项目负责人：」「编 制：」「审 核：」这类单元格，把图锚在它**右边一格**。

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

#: 签字栏标签（去掉空格后精确比对，避免把正文里的「编制说明」也当成签字位）
_LABELS = {
    "现场监测人",
    "监测人",
    "计算人",
    "校核人",
    "项目负责人",
    "编制",
    "审核",
    "项目负责",
}

#: 签名图高度（像素）。一行文字约 20px，签名稍大一点才像手写。
SIGN_HEIGHT_PX = 34


class SignatureError(RuntimeError):
    """签名文件本身有问题（格式不对、读不出来）。"""


def _signature_path() -> Path:
    return settings.signature_dir / "default.png"


def has_signature() -> bool:
    p = _signature_path()
    return p.is_file() and p.stat().st_size > 0


def save_signature(data: bytes) -> None:
    """保存签名图（统一转成 PNG，顺便裁掉四周大片空白）。

    为什么要裁：用户多半是手机拍一张纸，四周全是白边 —— 不裁的话贴到日报上
    会变成一小块字配一大片白，把表格撑变形。
    """
    from PIL import Image

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
    from PIL import ImageChops

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

    settings.signature_dir.mkdir(parents=True, exist_ok=True)
    img.save(_signature_path(), format="PNG")


def delete_signature() -> None:
    p = _signature_path()
    if p.exists():
        p.unlink()


def _is_sign_label(text: str) -> bool:
    """是不是签字栏标签。要求以冒号结尾 —— 正文里的「编制说明」不算。"""
    t = (text or "").strip().replace(" ", "").replace("\u3000", "").replace("\n", "")
    if not t.endswith(("：", ":")):
        return False
    core = t.rstrip("：:").strip()
    if core in _LABELS:
        return True
    # 「现场监测人：」「监 测 人：」这类带前缀的也算
    return any(core.endswith(lbl) for lbl in _LABELS if len(lbl) >= 3)


def stamp_workbook(wb) -> int:
    """把签名盖到工作簿所有签字栏。返回贴了几处（没签名返回 0）。"""
    if not has_signature():
        return 0

    from openpyxl.drawing.image import Image as XLImage

    path = _signature_path()
    stamped = 0

    for ws in wb.worksheets:
        targets: list[tuple[int, int]] = []
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and _is_sign_label(cell.value):
                    targets.append((cell.row, cell.column))

        for row_idx, col_idx in targets:
            # 贴到标签右边一格。
            # ⚠ 不要用 `col_idx >= ws.max_column` 跳过 —— 签字栏常常就在最后一列
            #   （如 J8「校核人：」），跳过会把最右边那处漏掉（测试抓到 3/4）。
            anchor = f"{get_column_letter(col_idx + 1)}{row_idx}"
            try:
                img = XLImage(str(path))
                # Image 打开后取尺寸：等比缩放，高度对齐一行文字
                w, h = img.width, img.height
                if h > 0:
                    img.height = SIGN_HEIGHT_PX
                    img.width = max(12, int(SIGN_HEIGHT_PX * w / h))
                ws.add_image(img, anchor)
                stamped += 1
            except Exception:  # noqa: BLE001 —— 某处贴不上不该影响整份日报
                continue

    return stamped
