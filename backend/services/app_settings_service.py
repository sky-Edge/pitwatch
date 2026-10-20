# -*- coding: utf-8 -*-
"""全站设置服务：默认规则套 / 默认母版 / 默认天气，以及"新建项目自动套用"。

用户 2026-10-11：「能不能把一些操作集中主界面上？每个项目都要调单独设置有点繁琐」

这里解决的是**第二层**——新建出来的项目不该是一张白纸：
默认规则套一绑，它立刻就能判预警；默认母版一挂，它立刻就能导日报。
新基坑进场时只要"建项目"，剩下两样不用再配一遍。

⚠ 只做"加上去"，**绝不覆盖项目已有的配置**：
用户手改过的规则/母版不能被一个默认值悄悄顶掉。
"""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models.app_setting import AppSetting

#: 配置键 → 默认值。新增一项就在这里加一行（不用改表结构）。
DEFAULTS: dict[str, object] = {
    #: 新建项目自动绑定的预警规则套（None = 不自动绑，靠通用规则兜底）
    "default_rule_set_id": None,
    #: 新建项目自动加入适用范围的母版（None = 不自动挂）
    "default_template_id": None,
    #: 新建项目导出日报时的默认天气
    "default_weather": "晴",
    #: 「文件检查」要扫的那个"共同文件夹"（导出日报统一存放处）
    "audit_folder": None,
    #: 上次检查完成的时间（ISO 字符串）—— 用来做"打开系统时若超过 7 天就补扫一遍"
    "audit_last_run": None,
    #: 自动生成数据时，是否允许生成会触发预警/报警的读数（2026-10-20 用户要的开关）
    "generate_allow_warning": True,
    #: 只读锁定：打开后后端拒绝一切写操作（给同事看/演示时用，防止误操作把数据改坏）
    "readonly_mode": False,
}


def _load(db: Session, key: str) -> object:
    row = db.get(AppSetting, key)
    if row is None or row.value is None:
        return DEFAULTS.get(key)
    try:
        return json.loads(row.value)
    except (TypeError, ValueError):
        # 值被手工改坏时退回默认值，不要让一句坏数据把整个设置接口带崩
        return DEFAULTS.get(key)


def _store(db: Session, key: str, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=payload))
    else:
        row.value = payload


def get_settings(db: Session) -> dict:
    """全站设置（缺的键用默认值补齐）。"""
    return {key: _load(db, key) for key in DEFAULTS}


def update_settings(db: Session, payload: dict) -> dict:
    """改设置（只改传进来的键）。会校验引用的规则套 / 母版是否真的存在。"""
    data = {k: v for k, v in (payload or {}).items() if k in DEFAULTS}
    # 路径类的空串一律存成 None：前端清空输入框会送 ""，存进去以后
    # "配了文件夹但其实是空的"会让补扫判断一直为真。
    for key, value in list(data.items()):
        if isinstance(value, str) and not value.strip():
            data[key] = None
    _validate_references(db, data)
    for key, value in data.items():
        _store(db, key, value)
    db.commit()
    return get_settings(db)


def _validate_references(db: Session, data: dict) -> None:
    """引用了不存在的规则套 / 母版 → 400，并说清是哪一个。"""
    if "default_rule_set_id" in data and data["default_rule_set_id"] is not None:
        from backend.models.warning_rule_set import WarningRuleSet

        sid = int(data["default_rule_set_id"])
        if db.get(WarningRuleSet, sid) is None:
            raise HTTPException(status_code=404, detail=f"预警规则套 {sid} 不存在")
        data["default_rule_set_id"] = sid

    if "default_template_id" in data and data["default_template_id"] is not None:
        from backend.models.export_template import ExportTemplate

        tid = int(data["default_template_id"])
        tpl = db.get(ExportTemplate, tid)
        if tpl is None:
            raise HTTPException(status_code=404, detail=f"母版 {tid} 不存在")
        if tpl.template_type != "excel":
            raise HTTPException(status_code=400, detail="只有 Excel 母版能设为默认导出母版")
        data["default_template_id"] = tid


def apply_defaults_to_new_project(db: Session, project_id: int) -> dict:
    """给新建的项目套上默认规则套 / 默认母版，返回实际做了什么（界面要如实告诉用户）。

    只"加上"，不覆盖：
    - 规则：把本项目**追加**进默认套的绑定列表（一个项目最多一套，追加即完成绑定）；
    - 母版：把本项目**追加**进默认母版的适用范围；通用母版本身已覆盖所有项目，不必追加。
    """
    from backend.services import export_template_service, warning_service

    cfg = get_settings(db)
    note: dict = {"rule_set": None, "template": None}

    sid = cfg.get("default_rule_set_id")
    if sid is not None:
        from backend.models.warning_rule_set import WarningRuleSet

        s = db.get(WarningRuleSet, int(sid))
        if s is not None:
            bound = warning_service.bound_project_ids(db, s.id)
            if project_id not in bound:
                warning_service.set_rule_set_projects(db, s.id, project_ids=[*bound, project_id])
            note["rule_set"] = s.name

    tid = cfg.get("default_template_id")
    if tid is not None:
        from backend.models.export_template import ExportTemplate

        tpl = db.get(ExportTemplate, tid)
        if tpl is not None:
            if tpl.is_global:
                # 通用母版已经覆盖所有项目，不用（也不该）再逐个项目绑
                note["template"] = f"{tpl.name}（通用）"
            else:
                bound = export_template_service.bound_project_ids(db, tpl.id)
                if project_id not in bound:
                    export_template_service.set_template_projects(
                        db, tpl.id, project_ids=[*bound, project_id]
                    )
                note["template"] = tpl.name

    return note
