# -*- coding: utf-8 -*-
"""全站设置的请求/响应模型。"""
from pydantic import BaseModel, Field


class AppSettingsRead(BaseModel):
    """全站设置（每个键都对齐 app_settings_service.DEFAULTS）。"""

    #: 新建项目自动绑定的预警规则套 id（None = 不绑）
    default_rule_set_id: int | None = None
    #: 新建项目自动加入适用范围的 Excel 母版 id（None = 不挂）
    default_template_id: int | None = None
    #: 新建项目的默认导出天气
    default_weather: str = "晴"
    #: 「文件检查」扫的那个共同文件夹（导出日报统一存放处）
    audit_folder: str | None = None
    #: 上次检查完成时间（ISO 字符串）—— 用来做"超过 7 天就提醒补扫"
    audit_last_run: str | None = None
    #: 自动生成数据时是否允许产生预警/报警读数（主界面开关）
    generate_allow_warning: bool = True
    #: 只读锁定：打开后所有写操作被拒绝
    readonly_mode: bool = False
    #: 天气位置（配了才自动取天气）
    weather_location: dict | None = None


class AppSettingsUpdate(BaseModel):
    """只改传进来的键（缺省即不动）。传 null 表示清空该项。"""

    default_rule_set_id: int | None = Field(default=None)
    default_template_id: int | None = Field(default=None)
    default_weather: str | None = Field(default=None, max_length=10)
    audit_folder: str | None = Field(default=None, max_length=500)
    audit_last_run: str | None = Field(default=None, max_length=40)
    generate_allow_warning: bool | None = Field(default=None)
    readonly_mode: bool | None = Field(default=None)
    weather_location: dict | None = Field(default=None)

    model_config = {"extra": "forbid"}
