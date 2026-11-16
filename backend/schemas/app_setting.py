# -*- coding: utf-8 -*-
"""全站设置的请求/响应模型。"""
from pydantic import BaseModel, Field, field_validator


class AppSettingsRead(BaseModel):
    """全站设置（每个键都对齐 app_settings_service.DEFAULTS）。"""

    #: 新建项目自动绑定的预警规则套 id（None = 不绑）
    default_rule_set_id: int | None = None
    #: 新建项目自动加入适用范围的 Excel 母版 id（None = 不挂）
    default_template_id: int | None = None
    #: 新建项目的默认导出天气
    #: 空串 = 自动（导出时按「天气位置」查当天天气）；用户 2026-11-16 要求默认自动
    default_weather: str = ""
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
    #: 测量仪器清单
    instruments: list[dict] | None = None
    #: 导出日报时签名的高度（像素，24~64）
    signature_size_px: int = 46
    #: 签名位置微调（像素，正数=往右/往下）
    signature_offset_x: int = 0
    signature_offset_y: int = 0

    @field_validator("default_weather", mode="before")
    @classmethod
    def _weather_none_as_auto(cls, v):
        """库里存成 NULL 时按「自动」处理，绝不让整个设置接口 500。

        2026-11-16 的事故：`update_settings` 把所有空串都落成 None，
        天气（空串 = 自动）被写成 NULL 后，这个字段要求 str，读接口直接 500 ——
        连带「设置」页、项目默认值弹窗全部打不开。写入侧已经收紧到只处理路径字段，
        这里再兜一道：老数据里已经存在的 NULL 也得能读出来。
        """
        return "" if v is None else v


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
    instruments: list[dict] | None = Field(default=None)
    signature_size_px: int | None = Field(default=None, ge=24, le=64)
    signature_offset_x: int | None = Field(default=None, ge=-80, le=80)
    signature_offset_y: int | None = Field(default=None, ge=-80, le=80)

    model_config = {"extra": "forbid"}
