# -*- coding: utf-8 -*-
"""测量仪器：导出日报时把「仪器型号 / 出厂编号 / 检定日期」写进报表。

用户 2026-11-13：「再加个仪器选择」。

【为什么不是每个项目一套仪器】
实际用法是：同一台仪器要跑好几个基坑，而同一份日报里**沉降用水准仪、位移用全站仪**
的情况也有。所以仪器做成**全站一份清单**，导出时选哪台就填哪台。
（不做成项目属性：项目上绑死了，换台仪器测就得改项目配置，反而更麻烦。）

【怎么写进母版】
母版里是**拼接好的字符串**：「仪器型号：Leica TZ08」「仪器出厂编号：1621037」
「检定日期：2025.10.20」—— 标签和值在同一个单元格里。
所以做法是：**保留标签，只替换冒号后面的部分**。整格重写会把字体/边框一起丢掉，
而且不同母版标签措辞不一样（有的写「仪器出厂编号」，有的写「出厂编号」）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from backend.services import app_settings_service

#: 常见仪器备选库：工程监测现场这几年真在用的型号。
#:
#: 用户 2026-11-13：「你把市面上的仪器都放在备选库了吧」—— 原先只有空白输入框，
#: 型号得自己敲（「Leica DNA03」打成「莱卡 DNA-03」就对不上验收资料）。
#: 这里给出下拉，选完只需补「出厂编号」和「检定日期」两个真正因人而异的字段。
#:
#: ⚠ 只写**厂牌 + 型号**，不写精度、量程这些参数：一类仪器不同批次参数不同，
#:   写死在库里反而会误导（且验收看的是检定证书，不是这里的字）。
#: ⚠ 这份表是**只读**的，用户自己的仪器仍然存在 app_settings.instruments 里；
#:   选了只是"预填"，存下来的是副本，之后随便改。
PRESET_CATALOG: list[dict] = [
    # —— 电子水准仪：沉降观测的主力，二等水准基本是这几台 ——
    {"category": "电子水准仪", "name": "电子水准仪", "model": "Leica DNA03"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "Leica DNA10"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "Leica Sprinter 250M"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "Trimble DiNi 03"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "Trimble DiNi 12"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "SOKKIA SDL30"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "SOKKIA SDL50"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "Topcon DL-502"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "Topcon DL-101C"},
    {"category": "电子水准仪", "name": "电子水准仪", "model": "南方 DL-2003"},
    # —— 光学水准仪：三、四等和施工抄平用 ——
    {"category": "光学水准仪", "name": "水准仪", "model": "苏州一光 DSZ2"},
    {"category": "光学水准仪", "name": "水准仪", "model": "苏州一光 DS3"},
    {"category": "光学水准仪", "name": "水准仪", "model": "博飞 DZS3-1"},
    {"category": "光学水准仪", "name": "水准仪", "model": "Leica NA2"},
    # —— 全站仪：水平位移、坐标放样 ——
    {"category": "全站仪", "name": "全站仪", "model": "Leica TS16"},
    {"category": "全站仪", "name": "全站仪", "model": "Leica TS15"},
    {"category": "全站仪", "name": "全站仪", "model": "Leica TS09 Plus"},
    {"category": "全站仪", "name": "全站仪", "model": "Leica TS02"},
    {"category": "全站仪", "name": "全站仪", "model": "Leica TM30"},
    {"category": "全站仪", "name": "全站仪", "model": "Leica TM50"},
    {"category": "全站仪", "name": "全站仪", "model": "Trimble S8"},
    {"category": "全站仪", "name": "全站仪", "model": "Trimble S9"},
    {"category": "全站仪", "name": "全站仪", "model": "Topcon MS05A"},
    {"category": "全站仪", "name": "全站仪", "model": "Topcon ES-52"},
    {"category": "全站仪", "name": "全站仪", "model": "SOKKIA NET05AXII"},
    {"category": "全站仪", "name": "全站仪", "model": "SOKKIA FX-101"},
    {"category": "全站仪", "name": "全站仪", "model": "南方 NTS-342R"},
    {"category": "全站仪", "name": "全站仪", "model": "南方 NTS-391R"},
    {"category": "全站仪", "name": "全站仪", "model": "中海达 ZTS-420R"},
    {"category": "全站仪", "name": "全站仪", "model": "宾得 R-202NE"},
    # —— 静力水准仪：自动化沉降，基坑周边建筑常用 ——
    {"category": "静力水准仪", "name": "静力水准仪", "model": "基康 BGK-4675LV"},
    {"category": "静力水准仪", "name": "静力水准仪", "model": "南京葛南 GN-1"},
    {"category": "静力水准仪", "name": "静力水准仪", "model": "长沙金码 JMZX-1000"},
    # —— 测斜仪：深层水平位移 ——
    {"category": "测斜仪", "name": "测斜仪", "model": "基康 GK-600"},
    {"category": "测斜仪", "name": "测斜仪", "model": "长沙金码 JMZX-7000"},
    # —— 其他 ——
    {"category": "其他", "name": "水位计", "model": "基康 BGK-4500S"},
    {"category": "其他", "name": "GNSS 接收机", "model": "Leica GS18"},
    {"category": "其他", "name": "GNSS 接收机", "model": "Trimble R12"},
]


def list_presets() -> list[dict]:
    """常见仪器备选库（只读）。前端按 category 分组显示。"""
    return [dict(item) for item in PRESET_CATALOG]


#: 标签 → 字段。按"包含"匹配，越靠前越优先（长标签先匹配，避免被短标签抢先）。
_LABEL_FIELDS: list[tuple[str, str]] = [
    ("仪器出厂编号", "serial"),
    ("出厂编号", "serial"),
    ("仪器编号", "serial"),
    ("仪器型号", "model"),
    ("设备型号", "model"),
    ("检定日期", "calibrated_on"),
    ("校准日期", "calibrated_on"),
]


def _fmt_date(value: str | None) -> str:
    """检定日期统一写成「2025.10.20」——母版里本来就是这种点分格式。"""
    if not value:
        return ""
    try:
        d = date.fromisoformat(value[:10])
        return f"{d.year}.{d.month:02d}.{d.day:02d}"
    except ValueError:
        return value


def list_instruments(db: Session) -> list[dict]:
    raw = app_settings_service.get_settings(db).get("instruments") or []
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": i,  # 用下标当 id：清单很短，不需要 uuid
                "name": item.get("name") or "",
                "model": item.get("model") or "",
                "serial": item.get("serial") or "",
                "calibrated_on": item.get("calibrated_on") or "",
            }
        )
    return out


def get_instrument(db: Session, instrument_id: int | None) -> dict | None:
    if instrument_id is None:
        return None
    items = list_instruments(db)
    if 0 <= instrument_id < len(items):
        return items[instrument_id]
    return None


def apply_to_workbook(wb, inst: dict) -> int:
    """把仪器信息写进工作簿。返回改了幾個单元格（0 = 这份母版没有仪器栏）。"""
    if not inst:
        return 0

    changed = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str) or "：" not in v and ":" not in v:
                    continue

                for label, field in _LABEL_FIELDS:
                    if label not in v:
                        continue
                    value = inst.get(field) or ""
                    if field == "calibrated_on":
                        value = _fmt_date(value)
                    if not value:
                        break

                    # 保留原来的分隔符（全角/半角冒号）
                    sep = "：" if "：" in v else ":"
                    head, _, after = v.partition(sep)
                    # 原文本冒号后有空格就保留（半角冒号的母版常写「仪器型号: xxx」）
                    space = " " if after.startswith(" ") else ""
                    new_text = f"{head}{sep}{space}{value}"
                    if new_text != v:
                        cell.value = new_text
                        changed += 1
                    break  # 一格只处理一个标签

    return changed
