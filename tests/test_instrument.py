# -*- coding: utf-8 -*-
"""测量仪器选择：导出日报时把选中的仪器写进报表。

用户 2026-11-13：「再加个仪器选择」。

判据：
  ① 仪器清单能存能读（全局一份，不是项目属性）；
  ② 导出时选了哪台就填哪台（型号/出厂编号/检定日期三处都要换）；
  ③ **没选仪器时不能动母版原来的值** —— 有些项目就是手填；
  ④ 日期要写成母版习惯的「2025.10.20」，不是 ISO。
"""
from __future__ import annotations

from openpyxl import Workbook

from backend.services import instrument_service

TWO = [
    {"name": "水准仪", "model": "Leica TZ08", "serial": "1621037", "calibrated_on": "2025-10-20"},
    {"name": "全站仪", "model": "Leica TS16", "serial": "8888888", "calibrated_on": "2026-03-15"},
]


def _save(client, items=TWO) -> None:
    r = client.put("/api/v1/settings", json={"instruments": items})
    assert r.status_code == 200, r.text


def test_save_and_list(client) -> None:
    _save(client)
    items = client.get("/api/v1/settings").json()["instruments"]
    assert len(items) == 2
    assert items[1]["model"] == "Leica TS16"


def test_get_by_index(client) -> None:
    _save(client)
    from backend.core.db import SessionLocal

    with SessionLocal() as db:
        assert instrument_service.get_instrument(db, 0)["serial"] == "1621037"
        assert instrument_service.get_instrument(db, 1)["serial"] == "8888888"
        assert instrument_service.get_instrument(db, 99) is None
        assert instrument_service.get_instrument(db, None) is None


def test_apply_replaces_all_three_fields() -> None:
    wb = Workbook()
    ws = wb.active
    ws["A4"] = "仪器型号：Leica TZ08"
    ws["F4"] = "仪器出厂编号：1621037"
    ws["K4"] = "检定日期：2025.10.20"

    changed = instrument_service.apply_to_workbook(wb, TWO[1])
    assert changed == 3
    assert ws["A4"].value == "仪器型号：Leica TS16"
    assert ws["F4"].value == "仪器出厂编号：8888888"
    assert ws["K4"].value == "检定日期：2026.03.15"


def test_no_instrument_leaves_master_untouched() -> None:
    """没选仪器 → 母版原来的值一个都不动。"""
    wb = Workbook()
    ws = wb.active
    ws["A4"] = "仪器型号：Leica TZ08"
    assert instrument_service.apply_to_workbook(wb, None) == 0
    assert ws["A4"].value == "仪器型号：Leica TZ08"


def test_half_width_colon_preserved() -> None:
    """母版用半角冒号时不要改成全角（样式会显得不统一）。"""
    wb = Workbook()
    ws = wb.active
    ws["A4"] = "仪器型号: old"
    instrument_service.apply_to_workbook(wb, TWO[0])
    assert ws["A4"].value == "仪器型号: Leica TZ08"


def test_unrelated_text_untouched() -> None:
    """正文里出现「仪器」但不是这几栏 → 不改。"""
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "本次监测使用仪器均为检定合格设备"
    assert instrument_service.apply_to_workbook(wb, TWO[0]) == 0


def test_date_format() -> None:
    assert instrument_service._fmt_date("2025-10-20") == "2025.10.20"
    assert instrument_service._fmt_date("2026-03-05") == "2026.03.05"
    assert instrument_service._fmt_date("") == ""
    assert instrument_service._fmt_date("乱写") == "乱写"


# ───────────────────── 常见仪器备选库 ─────────────────────


def test_presets_endpoint_lists_common_instruments(client) -> None:
    """备选库要能取到，且条数够用（用户：「把市面上的仪器都放在备选库了吧」）。"""
    r = client.get("/api/v1/settings/instrument-presets")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 20, f"备选库太少（{len(data)} 条），等于还得手打"

    for item in data:
        assert set(item) == {"category", "name", "model"}
        assert all(isinstance(item[k], str) and item[k].strip() for k in item)


def test_presets_cover_the_two_workhorses(client) -> None:
    """沉降靠水准仪、位移靠全站仪 —— 这两个品类不能缺。"""
    cats = {x["category"] for x in client.get("/api/v1/settings/instrument-presets").json()}
    assert "电子水准仪" in cats
    assert "全站仪" in cats


def test_presets_include_field_common_models(client) -> None:
    """现场最常报的两个型号必须在库里，否则搜索照样落空。"""
    models = {x["model"] for x in client.get("/api/v1/settings/instrument-presets").json()}
    assert "Leica DNA03" in models
    assert "Leica TS16" in models


def test_presets_have_no_duplicates(client) -> None:
    data = client.get("/api/v1/settings/instrument-presets").json()
    keys = [(x["category"], x["model"]) for x in data]
    assert len(keys) == len(set(keys)), "备选库里有重复条目"


def test_presets_are_readonly(client) -> None:
    """备选库是内置常量，取它不能顺手把用户自己的仪器清单改掉。"""
    client.get("/api/v1/settings/instrument-presets")
    assert client.get("/api/v1/settings").json()["instruments"] in (None, [])


def test_preset_choice_becomes_a_normal_instrument(client) -> None:
    """选了备选库里的型号后，存下来就是普通仪器 —— 用户可以接着改编号/日期。"""
    preset = client.get("/api/v1/settings/instrument-presets").json()[0]
    mine = {
        "name": preset["name"],
        "model": preset["model"],
        "serial": "SN-001",
        "calibrated_on": "2026-01-15",
    }
    client.put("/api/v1/settings", json={"instruments": [mine]})

    saved = client.get("/api/v1/settings").json()["instruments"]
    assert saved == [mine]
    assert saved[0]["model"] == preset["model"]
