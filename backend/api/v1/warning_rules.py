# -*- coding: utf-8 -*-
"""预警规则 API —— **规则库**形态（规则独立于项目，自己声明作用于哪些项目）。

- GET    /warning-rule-sets                    规则库清单（带适用项目与限值）
- POST   /warning-rule-sets                    新建一套（可从规范预设起步）
- PUT    /warning-rule-sets/{sid}              改名 / 改依据 / 改限值
- PUT    /warning-rule-sets/{sid}/projects     设置适用项目（多选 + 通用开关）
- DELETE /warning-rule-sets/{sid}              删除一套
- GET    /warning-rules/standards              规范预设列表（只读模板）
- GET    /projects/{pid}/warning-rules         该项目**生效**的规则（兼容入口）
- PUT    /projects/{pid}/warning-rules         从项目侧改规则（共用套会自动分叉）
- GET    /projects/{pid}/warning-rules/set     该项目用的是哪套（界面提示"改了会分叉"）

用户 2026-10-09：「把预警规则提出来 直接作用于系统 用预警规则来选择项目」
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.models.warning_rule_set import WarningRuleSet
from backend.schemas.warning_rule import (
    ProjectRuleSetInfo,
    StandardPreset,
    WarningRuleBatchWrite,
    WarningRuleRead,
    WarningRuleSetCreate,
    WarningRuleSetRead,
    WarningRuleSetScopeUpdate,
    WarningRuleSetUpdate,
)
from backend.services import warning_service
from backend.services.standards import PRESETS

project_rules_router = APIRouter(prefix="/projects/{project_id}/warning-rules", tags=["warning-rules"])
sets_router = APIRouter(prefix="/warning-rule-sets", tags=["warning-rules"])
standards_router = APIRouter(prefix="/warning-rules", tags=["warning-rules"])


def _project_names(db: Session) -> dict[int, str]:
    from sqlalchemy import select

    from backend.models.project import Project

    return {int(i): str(n) for i, n in db.execute(select(Project.id, Project.name))}


def _to_read(db: Session, s: WarningRuleSet, names: dict[int, str] | None = None) -> WarningRuleSetRead:
    pids = warning_service.bound_project_ids(db, s.id)
    name_of = names if names is not None else _project_names(db)
    return WarningRuleSetRead(
        id=s.id,
        name=s.name,
        source_note=s.source_note,
        is_global=bool(s.is_global),
        project_ids=pids,
        project_names=[name_of.get(p, f"项目#{p}") for p in pids],
        rules=[WarningRuleRead.model_validate(r) for r in warning_service.rules_of_set(db, s.id)],
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


# ─────────────────────────── 规则库 ───────────────────────────


@sets_router.get("", response_model=list[WarningRuleSetRead])
def list_rule_sets(db: Session = Depends(get_db)):
    """全部预警规则（含通用、含当前没绑任何项目的）。"""
    names = _project_names(db)
    return [_to_read(db, s, names) for s in warning_service.list_rule_sets(db)]


@sets_router.post("", response_model=WarningRuleSetRead)
def create_rule_set(payload: WarningRuleSetCreate, db: Session = Depends(get_db)):
    """新建一套预警规则，并指定它作用于哪些项目（或设为通用）。"""
    s = warning_service.create_rule_set(
        db,
        name=payload.name,
        data=WarningRuleBatchWrite(rules=payload.rules, source_note=payload.source_note),
        project_ids=payload.project_ids,
        is_global=payload.is_global,
        source_note=payload.source_note,
    )
    return _to_read(db, s)


@sets_router.put("/{set_id}", response_model=WarningRuleSetRead)
def update_rule_set(set_id: int, payload: WarningRuleSetUpdate, db: Session = Depends(get_db)):
    """改名 / 改依据 / 改限值。**改限值会影响所有绑定了这套规则的项目**（这正是共用规则的意义）。"""
    data = (
        WarningRuleBatchWrite(rules=payload.rules, source_note=payload.source_note)
        if payload.rules is not None
        else None
    )
    s = warning_service.update_rule_set(
        db, set_id, data=data, name=payload.name, source_note=payload.source_note
    )
    return _to_read(db, s)


@sets_router.put("/{set_id}/projects", response_model=WarningRuleSetRead)
def set_rule_set_projects(
    set_id: int, payload: WarningRuleSetScopeUpdate, db: Session = Depends(get_db)
):
    """设置这套规则作用于哪些项目（多选 + 通用开关）。

    一个项目最多绑一套：绑过来时会自动把它从原来那套上解绑。
    """
    s = warning_service.set_rule_set_projects(
        db, set_id, project_ids=payload.project_ids, is_global=payload.is_global
    )
    return _to_read(db, s)


@sets_router.delete("/{set_id}")
def delete_rule_set(set_id: int, db: Session = Depends(get_db)):
    """删除一套规则（会解除它与各项目的绑定）。"""
    warning_service.delete_rule_set(db, set_id)
    return {"deleted": True}


# ─────────────────────────── 规范预设（只读） ───────────────────────────


@standards_router.get("/standards", response_model=list[StandardPreset])
def list_standards():
    return PRESETS


# ─────────────────────────── 项目侧（兼容入口） ───────────────────────────


@project_rules_router.get("", response_model=list[WarningRuleRead])
def list_rules(project_id: int, db: Session = Depends(get_db)):
    """该项目**当前生效**的规则（绑定套 / 通用套 / 老式全局默认）。"""
    return warning_service.list_project_rules(db, project_id)


@project_rules_router.put("", response_model=list[WarningRuleRead])
def upsert_rules(project_id: int, payload: WarningRuleBatchWrite, db: Session = Depends(get_db)):
    """从项目侧改规则。**若这套规则还被别的项目共用，会自动新建一套只给本项目** ——
    否则改一个基坑的限值会把别的基坑一起改了。
    """
    return warning_service.upsert_project_rules(db, project_id, payload)


@project_rules_router.get("/set", response_model=ProjectRuleSetInfo)
def project_rule_set(project_id: int, db: Session = Depends(get_db)):
    """该项目用的是哪套规则、这套还被几个项目共用（界面据此提示"改了会分叉"）。"""
    from backend.services.project_service import get_project

    get_project(db, project_id)
    s = warning_service.rule_set_of_project(db, project_id)
    if s is None:
        g = warning_service.global_rule_set(db)
        if g is None:
            return ProjectRuleSetInfo()
        return ProjectRuleSetInfo(
            set_id=g.id,
            name=g.name,
            is_global=True,
            shared_project_count=len(warning_service.bound_project_ids(db, g.id)),
        )
    return ProjectRuleSetInfo(
        set_id=s.id,
        name=s.name,
        is_global=bool(s.is_global),
        shared_project_count=len(warning_service.bound_project_ids(db, s.id)),
    )
