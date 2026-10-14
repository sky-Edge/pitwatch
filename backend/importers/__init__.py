# -*- coding: utf-8 -*-
"""Excel Import Engine（导入引擎）——九步流程：上传→Sheet识别→列识别→字段映射→预览→校验→重复检查→确认→落库。

解析规则基于用户真实模板实测（八号基坑）：
- 双层表头（R5/R6），数据从 R7 起
- 位移表每测点 2 行：X 行有点号、Y 行点号空（继承上行点号）
- 读值单位 m（×1000 入 mm），变化量列已是 mm
- 观测日期在表头元信息「本次监测时间：YYYY-MM-DD」
"""
from backend.importers.parser import ParsedSheet, parse_workbook
from backend.importers.importer import preview_batch, confirm_batch

__all__ = ["ParsedSheet", "parse_workbook", "preview_batch", "confirm_batch"]
