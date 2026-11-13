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
    img.save(_role_dir() / name, format="PNG")


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
            anchor = f"{get_column_letter(col_idx + 1)}{row_idx}"
            try:
                img = XLImage(str(path))
                w, h = img.width, img.height
                if h > 0:
                    img.height = SIGN_HEIGHT_PX
                    img.width = max(12, int(SIGN_HEIGHT_PX * w / h))
                ws.add_image(img, anchor)
                stamped += 1
            except Exception:  # noqa: BLE001 —— 某处贴不上不该影响整份日报
                continue

    return stamped
