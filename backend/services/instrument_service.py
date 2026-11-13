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
