# -*- coding: utf-8 -*-
"""全站设置（键值表）。

为什么要有它（用户 2026-10-11）：
    「能不能把一些操作集中主界面上？每个项目都要调单独设置有点繁琐」

用户要的"少配一遍"分两层：
1. 已经独立出去的东西（预警规则库、母版库）该在**主界面**就能管；
2. 新建项目时该**自动带上**默认值 —— 默认规则套、默认母版、默认天气，
   不用每个项目再配一遍。

第 2 层需要一个"全站一份"的存放处。刻意做成最简单的 KV（`key` 唯一、`value` 存 JSON 字符串）：
这类"系统级小配置"以后还会加（比如默认仪器模式、默认缺失率），
每加一项就建一张表不值得；KV 表加一个键就行，也不用再写迁移。

⚠ 只放**系统级**配置。项目级的东西（正负号规则、测点、观测值）一律留在各自表里 ——
把两者混进一张 KV 表，迟早会出现"这个键到底是谁的"这种说不清的状态。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    #: 配置键（如 default_rule_set_id）
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: 配置值：**JSON 字符串**（None 存成 "null"，读回来还是 None）
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
