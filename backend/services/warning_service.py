# -*- coding: utf-8 -*-
"""预警规则服务（**规则库**形态：规则独立于项目，自己声明作用于哪些项目）。

用户 2026-10-09：「把预警规则提出来 直接作用于系统 用预警规则来选择项目」

- 一套规则 = 各指标 × 各等级的一组限值（现状 2×2 = 4 条），见 `warning_rule_sets`。
- 规则 → 项目 多对多（`warning_rule_set_projects`）；**一个项目最多绑一套**。
- `is_global` 的套作兜底：没绑任何套的项目用它。
- 规范预设（`standards.PRESETS`）只是**新建一套时的起点**，保存的始终是规则自己的行。

⚠ 读规则的地方一律走 `effective_rules(db, project_id)` —— 它是"生效规则"的**唯一出处**
（6 个调用方：算预警、逐期对比、生成数据、项目一句话结论、预警事件、报表控制值）。
本次改造刻意**保持它的签名与返回结构不变**，所以那些调用方一行都不用改。
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.warning_rule import WarningRule
from backend.models.warning_rule_set import WarningRuleSet, WarningRuleSetProject
from backend.schemas.warning_rule import WarningRuleBatchWrite, WarningRuleWrite
from backend.services.project_service import get_project


# ─────────────────────────── 规则库 ───────────────────────────


def list_rule_sets(db: Session) -> list[WarningRuleSet]:
    return list(db.scalars(select(WarningRuleSet).order_by(WarningRuleSet.id)))


def get_rule_set(db: Session, set_id: int) -> WarningRuleSet:
    s = db.get(WarningRuleSet, set_id)
    if s is None:
        raise HTTPException(status_code=404, detail="预警规则不存在")
    return s


def bound_project_ids(db: Session, set_id: int) -> list[int]:
    stmt = (
        select(WarningRuleSetProject.project_id)
        .where(WarningRuleSetProject.set_id == set_id)
        .order_by(WarningRuleSetProject.project_id)
    )
    return [int(x) for x in db.scalars(stmt)]


def bindings_for(db: Session, set_ids) -> dict[int, list[int]]:
    ids = [int(i) for i in set_ids]
    if not ids:
        return {}
    stmt = (
        select(WarningRuleSetProject.set_id, WarningRuleSetProject.project_id)
        .where(WarningRuleSetProject.set_id.in_(ids))
        .order_by(WarningRuleSetProject.set_id, WarningRuleSetProject.project_id)
    )
    out: dict[int, list[int]] = {}
    for sid, pid in db.execute(stmt):
        out.setdefault(int(sid), []).append(int(pid))
    return out


def rule_set_of_project(db: Session, project_id: int) -> WarningRuleSet | None:
    """该项目**绑定**的那套规则（没有绑定 → None）。一个项目最多一套。"""
    row = db.scalars(
        select(WarningRuleSetProject).where(WarningRuleSetProject.project_id == project_id)
    ).first()
    return db.get(WarningRuleSet, row.set_id) if row else None


def global_rule_set(db: Session) -> WarningRuleSet | None:
    """通用兜底那套（可能没有）。"""
    return db.scalars(
        select(WarningRuleSet).where(WarningRuleSet.is_global.is_(True)).order_by(WarningRuleSet.id)
    ).first()


def rules_of_set(db: Session, set_id: int) -> list[WarningRule]:
    stmt = (
        select(WarningRule)
        .where(WarningRule.set_id == set_id)
        .order_by(WarningRule.indicator, WarningRule.level)
    )
    return list(db.scalars(stmt))


def _assert_name_free(db: Session, name: str, exclude_id: int | None = None) -> None:
    stmt = select(WarningRuleSet).where(WarningRuleSet.name == name)
    if exclude_id is not None:
        stmt = stmt.where(WarningRuleSet.id != exclude_id)
    if db.scalars(stmt).first():
        raise HTTPException(
            status_code=400,
            detail=f"已经有一个叫「{name}」的预警规则了 —— 请换个名字（同名的两套在列表里分不出来）",
        )


def _write_rows(
    db: Session, set_id: int, data: WarningRuleBatchWrite, note: str | None
) -> None:
    """整组覆盖某套规则的行（先删后插，幂等）。"""
    for old in rules_of_set(db, set_id):
        db.delete(old)
    db.flush()
    for rule in data.rules:
        db.add(
            WarningRule(
                set_id=set_id,
                project_id=None,
                indicator=rule.indicator,
                level=rule.level,
                threshold_cumulative_mm=rule.threshold_cumulative_mm,
                threshold_rate_mm_per_day=rule.threshold_rate_mm_per_day,
                is_active=rule.is_active,
                source_note=rule.source_note or note or data.source_note,
            )
        )
    db.flush()


def set_rule_set_projects(
    db: Session,
    set_id: int,
    project_ids: list[int] | None = None,
    is_global: bool | None = None,
) -> WarningRuleSet:
    """设置这套规则作用于哪些项目。

    **一个项目最多绑一套** —— 绑过来时会自动把它从原来那套上解绑（同一个基坑不会同时
    吃两套限值）。要配成"哪儿都不用"也可以（通用开关一开就不依赖绑定）。
    """
    from backend.models.project import Project

    s = get_rule_set(db, set_id)
    if is_global is not None:
        s.is_global = bool(is_global)

    if project_ids is not None:
        wanted = sorted({int(p) for p in project_ids})
        if wanted:
            found = {int(x) for x in db.scalars(select(Project.id).where(Project.id.in_(wanted)))}
            missing = [p for p in wanted if p not in found]
            if missing:
                raise HTTPException(status_code=404, detail=f"项目不存在：{missing}")
        db.query(WarningRuleSetProject).filter(
            WarningRuleSetProject.set_id == set_id
        ).delete(synchronize_session=False)
        if wanted:
            # 先从别的套上解绑（一个项目只能有一套规则）
            db.query(WarningRuleSetProject).filter(
                WarningRuleSetProject.project_id.in_(wanted)
            ).delete(synchronize_session=False)
            for pid in wanted:
                db.add(WarningRuleSetProject(set_id=set_id, project_id=pid))

    db.commit()
    db.refresh(s)
    return s


def create_rule_set(
    db: Session,
    name: str,
    data: WarningRuleBatchWrite,
    project_ids: list[int] | None = None,
    is_global: bool = False,
    source_note: str | None = None,
) -> WarningRuleSet:
    name = (name or "").strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="请给这套预警规则起个名字")
    _assert_name_free(db, name)

    s = WarningRuleSet(name=name, source_note=source_note or data.source_note, is_global=is_global)
    db.add(s)
    db.flush()
    _write_rows(db, s.id, data, source_note)
    if project_ids:
        set_rule_set_projects(db, s.id, project_ids=project_ids)
    db.commit()
    db.refresh(s)
    return s


def update_rule_set(
    db: Session,
    set_id: int,
    data: WarningRuleBatchWrite | None = None,
    name: str | None = None,
    source_note: str | None = None,
) -> WarningRuleSet:
    s = get_rule_set(db, set_id)
    if name is not None and name.strip() and name.strip() != s.name:
        _assert_name_free(db, name.strip(), exclude_id=set_id)
        s.name = name.strip()
    if source_note is not None:
        s.source_note = source_note
    if data is not None:
        _write_rows(db, set_id, data, source_note)
    db.commit()
    db.refresh(s)
    return s


def delete_rule_set(db: Session, set_id: int) -> None:
    """删除一套规则：解除全部项目绑定 + 删掉规则行。"""
    s = get_rule_set(db, set_id)
    db.query(WarningRuleSetProject).filter(WarningRuleSetProject.set_id == set_id).delete(
        synchronize_session=False
    )
    for r in rules_of_set(db, set_id):
        db.delete(r)
    db.delete(s)
    db.commit()


# ─────────────────────────── 生效规则（唯一出处） ───────────────────────────


def effective_rules(db: Session, project_id: int) -> dict[str, dict[str, WarningRule]]:
    """项目**生效**规则：绑定的那套 → 通用套 → 老式规则行。

    返回 {indicator: {level: WarningRule}}，供 Warning Engine 使用。
    ⚠ 签名与返回结构与改造前完全一致 —— 6 个调用方（算预警/逐期对比/生成数据/
    项目结论/预警事件/报表控制值）因此一行都不用改。
    """
    merged: dict[str, dict[str, WarningRule]] = {"settlement": {}, "displacement": {}}

    def _apply(rows) -> None:
        for r in rows:
            if r.is_active:
                merged.setdefault(r.indicator, {})[r.level] = r

    # ① 老式全局默认（project_id IS NULL 且不属于任何套）—— 兼容底座
    _apply(db.scalars(
        select(WarningRule).where(
            WarningRule.project_id.is_(None), WarningRule.set_id.is_(None)
        )
    ).all())

    # ② 通用套（没绑任何规则的项目由它兜底）
    gset = global_rule_set(db)
    if gset is not None:
        _apply(rules_of_set(db, gset.id))

    # ③ 老式项目级行（project_id=本项目、不属于任何套）—— **迁移前的遗留数据**。
    #    这一层不能少：万一迁移没跑成（或有人手工插了老形态的行），缺了它这个项目就会
    #    突然"没有规则 → 不判预警"，等于悄悄漏报。保留它 = 老数据行为与改造前一致。
    _apply(db.scalars(
        select(WarningRule).where(
            WarningRule.project_id == project_id, WarningRule.set_id.is_(None)
        )
    ).all())

    # ④ 该项目绑定的那套（优先级最高）
    own = rule_set_of_project(db, project_id)
    if own is not None:
        _apply(rules_of_set(db, own.id))

    return merged


def list_project_rules(db: Session, project_id: int) -> list[WarningRule]:
    """该项目**当前生效**的规则行（绑定套 / 通用套 / 老式规则行）。

    界面上的「预警规则」弹窗读的就是它 —— 用户看到什么数，系统就按什么判。
    """
    get_project(db, project_id)
    own = rule_set_of_project(db, project_id)
    if own is not None:
        return rules_of_set(db, own.id)
    legacy = list(db.scalars(
        select(WarningRule)
        .where(WarningRule.project_id == project_id, WarningRule.set_id.is_(None))
        .order_by(WarningRule.indicator, WarningRule.level)
    ))
    if legacy:
        return legacy
    gset = global_rule_set(db)
    if gset is not None:
        return rules_of_set(db, gset.id)
    return list(db.scalars(
        select(WarningRule)
        .where(WarningRule.project_id.is_(None), WarningRule.set_id.is_(None))
        .order_by(WarningRule.indicator, WarningRule.level)
    ))


def upsert_project_rules(db: Session, project_id: int, data: WarningRuleBatchWrite) -> list[WarningRule]:
    """把规则写回**该项目绑定的那套**（从项目侧改规则时用）。

    ⚠ **共用套自动分叉**：如果这套规则还被别的项目用着，绝不能就地改 ——
    改一次会把别的基坑的限值一起改掉。此时**复制出一套只绑本项目的**再改，
    原套与其它项目原样不动。（"改一次全体跟随"只在规则库里直接编辑那套时发生。）
    """
    project = get_project(db, project_id)
    own = rule_set_of_project(db, project_id)

    if own is None:
        own = create_rule_set(
            db,
            name=_unique_set_name(db, f"{project.name} 的预警规则"),
            data=data,
            project_ids=[project_id],
        )
        return rules_of_set(db, own.id)

    others = [p for p in bound_project_ids(db, own.id) if p != project_id]
    if others:
        forked = _clone_for_project(db, own, project, data)
        return rules_of_set(db, forked.id)

    _write_rows(db, own.id, data, data.source_note)
    db.commit()
    return rules_of_set(db, own.id)


def _unique_set_name(db: Session, base: str) -> str:
    name = base[:100]
    n = 2
    while db.scalars(select(WarningRuleSet).where(WarningRuleSet.name == name)).first():
        name = f"{base[:94]}（{n}）"
        n += 1
    return name


def _clone_for_project(
    db: Session, src: WarningRuleSet, project, data: WarningRuleBatchWrite
) -> WarningRuleSet:
    """把 src 复制成一套只绑该项目的规则，并写入新值。

    ⚠ 必须**先解绑**原套再绑新套：`warning_rule_set_projects.project_id` 是唯一的
    （一个项目最多一套），不解绑会直接撞唯一约束。
    """
    new_set = WarningRuleSet(
        name=_unique_set_name(db, f"{project.name} 的预警规则"),
        source_note=data.source_note or src.source_note,
        is_global=False,
    )
    db.add(new_set)
    db.flush()
    db.query(WarningRuleSetProject).filter(
        WarningRuleSetProject.project_id == project.id
    ).delete(synchronize_session=False)
    db.add(WarningRuleSetProject(set_id=new_set.id, project_id=project.id))
    _write_rows(db, new_set.id, data, new_set.source_note)
    db.commit()
    db.refresh(new_set)
    return new_set


def report_control_values(db: Session, project_id: int, indicator: str) -> tuple[float | None, float | None]:
    """报表里「控制值·累计变化量 / 变化速率」两列的**唯一出处**。

    = 项目生效规则里 `control` 级的那两个阈值。报表上只有"控制值"这一组列（没有报警值），
    所以这里只取 control；判定用的也是同一对数字——**表格与规则从此同源**，
    不会再出现"表里写 30/10、系统里按 20/2 判"这种对不上的情况。

    没配规则 → (None, None)。**调用方自己决定回退（母版原值），绝不在这里瞎给 0。**
    """
    rule = effective_rules(db, project_id).get(indicator, {}).get("control")
    if rule is None:
        return None, None
    return rule.threshold_cumulative_mm, rule.threshold_rate_mm_per_day


def sync_control_rules(
    db: Session,
    project_id: int,
    values: dict[str, tuple[float | None, float | None]],
    note: str = "自台账控制值同步",
) -> tuple[list[WarningRule], list[dict]]:
    """把台账/清单里读到的「控制值」写进项目预警规则。返回 (规则列表, 变更明细)。

    **只动 `control` 级，别的什么都不碰。** 台账里只有"控制值"这一对数字
    （累计 / 速率），报警值在表里毫无依据 —— 凭它推算报警值就是编数据。

    另外：这里以**该项目当前生效的规则**为底（不是别的项目的），只有本次有值的指标
    才覆盖对应字段，其余原样保留。写回时若那套规则还被别的项目共用，会**自动分叉**
    （复制一套只绑本项目再改）—— 否则导入一个基坑的台账会把别的基坑限值一起改掉。
    """
    existing = list_project_rules(db, project_id)
    # (indicator, level) → 该规则的四个可写字段
    rows: dict[tuple[str, str], dict] = {
        (r.indicator, r.level): {
            "threshold_cumulative_mm": r.threshold_cumulative_mm,
            "threshold_rate_mm_per_day": r.threshold_rate_mm_per_day,
            "is_active": r.is_active,
            "source_note": r.source_note,
        }
        for r in existing
    }

    changes: list[dict] = []
    for indicator, (cum, rate) in values.items():
        if indicator not in ("settlement", "displacement"):
            continue
        if cum is None and rate is None:
            continue
        key = (indicator, "control")
        cur = rows.get(key)
        old_cum = cur["threshold_cumulative_mm"] if cur else None
        old_rate = cur["threshold_rate_mm_per_day"] if cur else None
        new_cum = cum if cum is not None else old_cum
        new_rate = rate if rate is not None else old_rate
        if cur is not None and new_cum == old_cum and new_rate == old_rate:
            continue  # 一致 → 不动（别把"没变化"也记成一笔变更）
        changes.append(
            {
                "indicator": indicator,
                "old_cum": old_cum,
                "old_rate": old_rate,
                "new_cum": new_cum,
                "new_rate": new_rate,
                "created": cur is None,
            }
        )
        rows[key] = {
            "threshold_cumulative_mm": new_cum,
            "threshold_rate_mm_per_day": new_rate,
            "is_active": True,
            "source_note": note,
        }

    if not changes:
        return existing, []

    payload = WarningRuleBatchWrite(
        rules=[
            WarningRuleWrite(indicator=i, level=lvl, **fields) for (i, lvl), fields in rows.items()
        ],
        source_note=note,
    )
    return upsert_project_rules(db, project_id, payload), changes
