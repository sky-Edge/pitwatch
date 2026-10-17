# -*- coding: utf-8 -*-
"""行业规范预设库（只读模板，Warning Engine 的规则来源）。

数据依据：
- GB 50497-2019《建筑基坑工程监测技术标准》表 8.0.4（钢板桩/灌注桩/地下连续墙等
  支护结构顶部位移报警值），双控原则：累计值 + 变化速率，任一超标即预警。
- 报警值惯例：预警(control)取规范限值下沿，报警(alarm)取上沿；
  具体数值应以设计文件为准，用户可在界面上自行调整。
- 用户项目模板（八号基坑实测）：沉降控制 30mm/10mm·d⁻¹、位移 20mm/3mm·d⁻¹，
  作为"按本项目实际制定"的示例。
- **「本次变化量 ΔV」这一档预设里一律留空**：规范只给累计与速率，没有"单期跳变量"的限值。
  留空 = 该维不判（见 `warnings.engine.check_level`）—— 拿速率×1 天替项目编一个上限，
  就违反了这个项目"不许凭空造阈值"的底线；现场要卡这一维，自己按方案填。
"""
from backend.schemas.warning_rule import StandardPreset, WarningRuleWrite

# 支护类型：拉森钢板桩（用户主场景）
_LZ = "拉森钢板桩支护"


def _rules(
    indicator: str,
    control_cum: float | None,
    control_rate: float | None,
    alarm_cum: float | None,
    alarm_rate: float | None,
    note: str,
) -> list[WarningRuleWrite]:
    return [
        WarningRuleWrite(
            indicator=indicator,
            level="control",
            threshold_cumulative_mm=control_cum,
            threshold_rate_mm_per_day=control_rate,
            is_active=True,
            source_note=note,
        ),
        WarningRuleWrite(
            indicator=indicator,
            level="alarm",
            threshold_cumulative_mm=alarm_cum,
            threshold_rate_mm_per_day=alarm_rate,
            is_active=True,
            source_note=note,
        ),
    ]


PRESETS: list[StandardPreset] = [
    StandardPreset(
        key="gb50497_l1",
        name="GB 50497-2019 建筑基坑 · 一级",
        description=f"{_LZ}：围护墙顶水平位移累计 20~30mm / 速率 2~3mm·d⁻¹；竖向位移累计 10~20mm / 速率 2~3mm·d⁻¹",
        rules=_rules(
            "displacement", 20, 2, 30, 3, "GB 50497-2019 表8.0.4 一级（设计限值取下限，请以设计文件为准）",
        )
        + _rules(
            "settlement", 10, 2, 20, 3, "GB 50497-2019 表8.0.4 一级（设计限值取下限，请以设计文件为准）",
        ),
    ),
    StandardPreset(
        key="gb50497_l2",
        name="GB 50497-2019 建筑基坑 · 二级",
        description=f"{_LZ}：围护墙顶水平位移累计 30~40mm / 速率 2~4mm·d⁻¹；竖向位移累计 20~30mm / 速率 2~3mm·d⁻¹",
        rules=_rules(
            "displacement", 30, 2, 40, 4, "GB 50497-2019 表8.0.4 二级（设计限值取下限，请以设计文件为准）",
        )
        + _rules(
            "settlement", 20, 2, 30, 3, "GB 50497-2019 表8.0.4 二级（设计限值取下限，请以设计文件为准）",
        ),
    ),
    StandardPreset(
        key="gb50497_l3",
        name="GB 50497-2019 建筑基坑 · 三级",
        description=f"{_LZ}：围护墙顶水平位移累计 40~60mm / 速率 3~5mm·d⁻¹；竖向位移累计 30~40mm / 速率 3~4mm·d⁻¹",
        rules=_rules(
            "displacement", 40, 3, 60, 5, "GB 50497-2019 表8.0.4 三级（设计限值取下限，请以设计文件为准）",
        )
        + _rules(
            "settlement", 30, 3, 40, 4, "GB 50497-2019 表8.0.4 三级（设计限值取下限，请以设计文件为准）",
        ),
    ),
    StandardPreset(
        key="custom_8hao",
        name="八号基坑实测模板（自定义）",
        description="用户项目实际采用的限值：沉降累计 30mm / 速率 10mm·d⁻¹；水平位移累计 20mm / 速率 3mm·d⁻¹",
        rules=_rules(
            "displacement", 20, 3, 30, 4, "八号基坑监测方案（自定义）",
        )
        + _rules(
            "settlement", 30, 10, 40, 12, "八号基坑监测方案（自定义）",
        ),
    ),
    StandardPreset(
        key="blank",
        name="自定义（空白）",
        description="不加载预设数值，由用户逐项填写超限范围",
        rules=_rules("displacement", None, None, None, None, "自定义") + _rules("settlement", None, None, None, None, "自定义"),
    ),
]


def get_preset(key: str) -> StandardPreset | None:
    return next((p for p in PRESETS if p.key == key), None)
