# -*- coding: utf-8 -*-
"""Report Engine —— 成果导出（Excel 日报 / Word 日报）。

核心约定（对应 Phase 0 设计）：
1. 导出即读取：只读 observation_records / warning_events 等，不包含任何计算逻辑；
   计算值（ΔV/C/Rate/预警）取自 Calculation/Warning Engine 的结果，保证与页面一致。
2. Excel 按用户真实模板版式生成（八号基坑）：竖向位移 + r.立柱结构水平位移 两个数据 Sheet，
   双层表头（R5/R6）、数据从 R7 起、读值单位 m、变化量 mm、控制值内嵌、预警等级列自动填。
3. 超限/预警行按工程惯例标红（红=超限警示）。
"""
from backend.reports.excel_report import build_period_excel
from backend.reports.docx_report import build_daily_docx

__all__ = ["build_period_excel", "build_daily_docx"]
