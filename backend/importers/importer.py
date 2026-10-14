# -*- coding: utf-8 -*-
"""导入引擎编排（九步流程的「校验→重复检查→确认→落库」阶段）。

流程：上传文件 → 解析（Sheet 识别/列识别/数据提取）→ 预览 → 校验 → 重复检查 → 用户确认 → 落库。

本模块负责：
1. preview_batch —— 对已上传解析的批次做「校验 + 重复检查」，返回可导入/冲突/错误清单；
2. confirm_batch —— 用户确认后正式落库（source=excel，绑定 import_batch_id）；
3. 单位换算已由 parser 完成（m → mm ×1000）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.importers.parser import ParsedSheet, parse_workbook
from backend.importers.point_matcher import suggest_match
from backend.models.import_batch import ImportBatch
from backend.models.observation_record import ObservationRecord
from backend.repositories import point_repo


def save_upload(file_bytes: bytes, filename: str) -> Path:
    """保存上传文件到暂存目录，返回路径。

    目录取自 `settings.upload_dir`（可用 MONITOR_UPLOAD_DIR 覆盖）——
    不要在这里写死路径：测试必须能把上传目录指到临时目录，
    否则测试用的假台账会混进生产上传目录。
    """
    upload_dir = settings.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    path = upload_dir / safe_name
    path.write_bytes(file_bytes)
    return path


def analyze_file(path: str | Path) -> list[ParsedSheet]:
    """解析上传文件（供上传接口立即返回识别结果）。"""
    return parse_workbook(path)


def record_file_path(batch: ImportBatch, path: str | Path) -> None:
    """把原始文件路径记进批次（field_mapping，JSON 列，不动表结构）。

    用户改观测日期时要按新日期**重新核对**，而校验必须基于原始行数据 ——
    批次表里只存了统计与列映射，所以得留个路径回去重解析。
    """
    mapping = dict(batch.field_mapping or {})
    mapping["file_path"] = str(path)
    batch.field_mapping = mapping


def apply_sheet_dates(
    sheets: list[ParsedSheet], overrides: dict[str, str | None]
) -> list[ParsedSheet]:
    """按用户指定的日期覆盖各 Sheet 的观测日期（原地修改并返回）。

    overrides：{sheet_name: 'YYYY-MM-DD' | None}；未出现在字典里的表保持原样，
    值为 None/'' 表示清空成"未指定日期"。日期格式不对直接抛 ValueError，
    由 API 层转 400 —— 不能让脏日期溜进预览，否则会算出错误的冲突判定。
    """
    for sheet in sheets:
        if sheet.sheet_name not in overrides:
            continue
        raw = overrides[sheet.sheet_name]
        if raw in (None, ""):
            sheet.obs_date = None
            continue
        try:
            sheet.obs_date = date.fromisoformat(str(raw))
        except ValueError as e:
            raise ValueError(f"数据表「{sheet.sheet_name}」的观测日期格式不正确：{raw}") from e
    return sheets


def recheck_with_dates(
    batch: ImportBatch, overrides: dict[str, str | None]
) -> tuple[list[ParsedSheet], dict[str, str | None]]:
    """按用户指定的日期重新解析原始文件 + 重跑校验，返回 (解析结果, 表里原日期)。

    与 upload 的区别只在"日期从哪来"：upload 用表里读到的，这里用用户指定的。
    同时返回每张表**原本识别到的**日期（已覆盖就丢了），前端要拿它显示
    "表里写的是 X，你改成了 Y"，用户才知道自己改的到底有没有生效。
    找不到原始文件时抛 FileNotFoundError（API 层转成人话提示）。
    """
    path = (batch.field_mapping or {}).get("file_path")
    if not path or not Path(path).is_file():
        raise FileNotFoundError(
            "这个批次没有保留原始文件，无法按新日期重新核对，请重新上传该文件"
        )
    sheets = parse_workbook(path)
    originals = {s.sheet_name: (s.obs_date.isoformat() if s.obs_date else None) for s in sheets}
    sheets = apply_sheet_dates(sheets, overrides)
    mapping = dict(batch.field_mapping or {})
    # 记下用户指定过的日期（审计：能回答"这批数据当时为什么落在这天"）
    mapping["date_overrides"] = {k: v for k, v in overrides.items() if v}
    batch.field_mapping = mapping
    return sheets, originals


def _check_point_series_component(
    db: Session, project_id: int, point_no: str, series_type: str, component: str
) -> tuple[bool, str | None]:
    """校验测点存在性与 series/component 匹配。返回 (ok, 错误信息)。"""
    point = point_repo.get_by_no(db, project_id, point_no)
    if point is None:
        return False, f"测点 {point_no} 不存在于本项目"
    if point.status == "inactive":
        return False, f"测点 {point_no} 已停用"
    if series_type == "settlement" and component != "value":
        return False, f"测点 {point_no} 不支持沉降分量 {component}"
    if series_type == "displacement" and component not in ("X", "Y"):
        return False, f"测点 {point_no} 不支持位移分量 {component}"
    # 测点类型与系列匹配
    if point.point_type == "settlement" and series_type != "settlement":
        return False, f"测点 {point_no} 为沉降点，不支持位移观测"
    if point.point_type == "displacement" and series_type != "displacement":
        return False, f"测点 {point_no} 为位移点，不支持沉降观测"
    return True, None


def _find_existing(
    db: Session, point_no: str, series_type: str, component: str, obs_date, project_id: int
) -> ObservationRecord | None:
    """重复检查：查该点该分量当日已存在的生效记录（未被更正替代）。"""
    point = point_repo.get_by_no(db, project_id, point_no)
    if point is None:
        return None
    return db.query(ObservationRecord).filter(
        ObservationRecord.point_id == point.id,
        ObservationRecord.series_type == series_type,
        ObservationRecord.component == component,
        ObservationRecord.obs_date == obs_date,
        ObservationRecord.superseded_by_id.is_(None),
    ).first()


def _control_suggestions(db: Session, project_id: int, sheets: list[ParsedSheet]) -> list[dict]:
    """汇总台账里写着的「控制值」，并与项目当前预警规则对照。

    真实台账的「控制值」是**两张表各一组**（竖向位移：累计/速率；水平位移：累计/速率），
    同一张表里每一行都重复写着同一对数字。取出现次数最多的那一对（多数票），
    避免个别手改行或表尾小把戏把值带偏。

    为什么要对照：台账里写着 30/10，而项目可能压根没配规则（批量建项目的项目就是这样），
    或者配的是别的数 —— 报表里写的和系统判定的必须是一个数。
    """
    from collections import Counter

    from backend.services.warning_service import report_control_values

    per: dict[str, Counter] = {}
    for sheet in sheets:
        for row in sheet.rows:
            if row.control_cum_mm is None and row.control_rate_mm_d is None:
                continue
            per.setdefault(row.series_type, Counter())[(row.control_cum_mm, row.control_rate_mm_d)] += 1

    out: list[dict] = []
    for indicator in ("settlement", "displacement"):
        counter = per.get(indicator)
        if not counter:
            continue
        (cum, rate), hits = counter.most_common(1)[0]
        cur_cum, cur_rate = report_control_values(db, project_id, indicator)
        out.append(
            {
                "indicator": indicator,
                "control_cum_mm": cum,
                "control_rate_mm_d": rate,
                "row_count": sum(counter.values()),
                "matched_rows": hits,
                "current_cum_mm": cur_cum,
                "current_rate_mm_d": cur_rate,
                # 一致的不用提示；不一致（含"还没配规则"）才需要用户拿主意
                "differs": (cum != cur_cum) or (rate != cur_rate),
                "has_rule": cur_cum is not None or cur_rate is not None,
            }
        )
    return out


def preview_batch(db: Session, batch: ImportBatch, sheets: list[ParsedSheet]):
    """对解析结果做「校验 + 重复检查」。

    返回结构化预览：
    - valid_rows:   可导入行
    - conflict_rows: 已存在同日记录（跳过，不覆盖 —— 原始数据可信）
    - error_rows:   校验失败行（测点不存在/停用/类型不匹配）
    """
    project_id = batch.project_id
    valid: list[dict] = []
    conflicts: list[dict] = []
    overrides: list[dict] = []
    errors: list[dict] = []
    no_date_rows: list[dict] = []
    seen: set[tuple] = set()  # 批次内去重

    existing_nos = [p.point_no for p in point_repo.list_by_project(db, project_id, include_inactive=True)]
    # 未知点号聚合（需求②）：excel_no → {series_types, components, is_v0_hint}
    unknown_map: dict[str, dict] = {}

    for sheet in sheets:
        for row in sheet.rows:
            key = (row.point_no, row.series_type, row.component, sheet.obs_date)
            item = {
                "point_no": row.point_no,
                "series_type": row.series_type,
                "component": row.component,
                "obs_date": sheet.obs_date.isoformat() if sheet.obs_date else None,
                "sheet_name": sheet.sheet_name,
                # 该行所属数据表没有观测日期（如「名字 + X/Y/Z」初始值清单）：
                # 无论最终归入哪个分组（有效/冲突/未知点错误），都带上此标记，
                # 前端据此弹窗让用户指定日期后再导入。
                "needs_date": sheet.obs_date is None,
                "value_mm": row.current_mm,
                "initial_mm": row.initial_mm,
                "remark": row.remark,
                "warning_level": row.warning_level,
                "control_cum_mm": row.control_cum_mm,
                "control_rate_mm_d": row.control_rate_mm_d,
            }
            # 测点是否存在（优先于 series/component 校验，用于区分"未知点"与"类型不匹配"）
            point = point_repo.get_by_no(db, project_id, row.point_no)
            if point is None:
                # 未知点：聚合用于"自动建点/映射"建议
                entry = unknown_map.setdefault(
                    row.point_no,
                    {"point_no": row.point_no, "series_types": set(), "components": set(), "row_count": 0},
                )
                entry["series_types"].add(row.series_type)
                entry["components"].add(row.component)
                entry["row_count"] += 1
                item["error"] = f"测点 {row.point_no} 不存在于本项目"
                item["error_kind"] = "unknown_point"
                item["suggested"] = suggest_match(row.point_no, existing_nos)
                errors.append(item)
                continue
            ok, err = _check_point_series_component(db, project_id, row.point_no, row.series_type, row.component)
            if not ok:
                errors.append({**item, "error": err, "error_kind": "invalid"})
                continue
            if key in seen:
                errors.append({**item, "error": "批次内重复行", "error_kind": "invalid"})
                continue
            seen.add(key)
            if sheet.obs_date is None:
                # 未识别到观测日期：不整批拒绝。标记为 no_date，
                # 前端弹窗让用户指定观测日期后再确认导入。
                item["error"] = "未识别到观测日期，请为该批数据指定观测日期"
                item["error_kind"] = "no_date"
                no_date_rows.append(item)
                continue
            existing = _find_existing(db, row.point_no, row.series_type, row.component, sheet.obs_date, project_id)
            if existing:
                item["existing_value_mm"] = existing.value_mm
                item["existing_record_id"] = existing.id
                # 系统「自动生成」的数据不作为基准：导入时以用户数据为准，允许覆盖该条；
                # 若已有记录来自导入/手工录入（真实数据）→ 仍按冲突处理，绝不覆盖。
                if existing.source == "generated":
                    item["overridable"] = True
                    overrides.append(item)
                else:
                    conflicts.append(item)
            else:
                valid.append(item)

    # 汇总统计写入批次（落库阶段再更新 imported_rows）
    batch.valid_rows = len(valid)
    batch.conflict_rows = len(conflicts)
    batch.total_rows = len(valid) + len(conflicts) + len(overrides) + len(errors)
    db.add(batch)
    db.commit()

    # 未知点聚合结果序列化：推断类型 = 出现过的 series_type 并集映射
    type_of_series = {
        frozenset({"settlement"}): "settlement",
        frozenset({"displacement"}): "displacement",
        frozenset({"settlement", "displacement"}): "both",
    }
    point_issues = []
    for no, e in unknown_map.items():
        sts = frozenset(e["series_types"])
        # 组件含 X/Y → 也提示位移
        point_issues.append({
            "point_no": no,
            "row_count": e["row_count"],
            "series_types": sorted(e["series_types"]),
            "components": sorted(e["components"]),
            "infer_point_type": type_of_series.get(sts, "both"),
            "suggested": suggest_match(no, existing_nos),
        })

    return {
        "valid_rows": valid,
        "override_rows": overrides,
        "conflict_rows": conflicts,
        "error_rows": errors,
        "no_date_rows": no_date_rows,
        "point_issues": point_issues,
        "control_suggest": _control_suggestions(db, batch.project_id, sheets),
    }


def apply_point_actions(db: Session, project_id: int, actions: list[dict]) -> dict:
    """处理用户对未知点号的动作（需求②）。

    action 结构：{excel_no, auto_create, infer_point_type, map_to}
    - auto_create=True：以 excel_no 为编号按 infer_point_type 创建测点；
    - map_to=项目内已有编号：不建点（重写由 confirm 阶段对行做 point_no 映射）；
    - 返回 {created:[], mapped:[], skipped:[]}（skipped=动作无效）。
    """
    from backend.schemas.point import PointCreate

    created: list[str] = []
    mapped: list[str] = []
    skipped: list[str] = []

    existing_nos = {p.point_no for p in point_repo.list_by_project(db, project_id, include_inactive=True)}
    for a in actions:
        excel_no = a.get("excel_no", "")
        if not excel_no:
            continue
        map_to = a.get("map_to") or ""
        auto = bool(a.get("auto_create"))
        # 映射目标必须在项目已存在；自动建点目标不能与已有冲突
        if not auto and map_to:
            if map_to in existing_nos:
                mapped.append(excel_no)
            else:
                skipped.append(excel_no)
            continue
        if auto:
            if excel_no in existing_nos:
                skipped.append(excel_no)  # 已存在 → 交由映射
                continue
            try:
                point_repo.create(
                    db,
                    project_id,
                    PointCreate(point_no=excel_no, point_type=a.get("infer_point_type") or "both"),
                )
                existing_nos.add(excel_no)
                created.append(excel_no)
            except Exception:  # noqa: BLE001
                db.rollback()
                skipped.append(excel_no)
            continue
        skipped.append(excel_no)
    db.commit()
    return {"created": created, "mapped": mapped, "skipped": skipped}


def confirm_batch(
    db: Session,
    batch: ImportBatch,
    valid_rows: list[dict],
    point_actions: list[dict] | None = None,
    sync_control_values: bool = False,
) -> dict:
    """正式落库：仅导入 valid_rows（用户已确认）。

    point_actions：可选，先处理未知点号（建点/映射），再把映射关系应用到行后导入。
    覆盖规则（用户口径：以导入的数据为准，不以系统编的数据为准）：
    - 同点同日已有「导入 / 手工录入」记录 → 跳过，绝不覆盖（真实数据可信）；
    - 同点同日已有「系统自动生成」记录 → 以本次导入为准替换，旧记录标记 superseded 留痕。

    返回 {"imported": n, "replaced_generated": m, "skipped_dup": k}
    """
    # 1) 处理点动作（建点/映射），并建立 excel_no → point_no 的重写表
    remap: dict[str, str] = {}
    created_counts: dict[str, int] = {"created": 0, "mapped": 0, "skipped": 0}
    if point_actions:
        res = apply_point_actions(db, batch.project_id, point_actions)
        created_counts = {k: len(v) for k, v in res.items()}
        for a in point_actions:
            if not a.get("auto_create") and a.get("map_to"):
                remap[a["excel_no"]] = a["map_to"]

    imported = 0
    replaced = 0
    skipped_dup = 0
    skipped_no_date = 0
    for item in valid_rows:
        # 无观测日期的行无法入库（观测记录必有日期）。正常流程下前端会先把用户
        # 指定的观测日期补上；这里兜底并计数，绝不静默丢行（用户能看到缺了几条）。
        if not item.get("obs_date"):
            skipped_no_date += 1
            continue
        point_no = remap.get(item["point_no"], item["point_no"])
        point = point_repo.get_by_no(db, batch.project_id, point_no)
        if point is None:
            continue
        # 重复保护：该点该分量该日已有生效记录 → 跳过（不覆盖）。补日期行走
        # 预览外的新日期，可能撞已有记录，这里兜底防重复入库。
        obs_date_parsed = datetime.fromisoformat(item["obs_date"]).date()
        dup = db.query(ObservationRecord).filter(
            ObservationRecord.point_id == point.id,
            ObservationRecord.series_type == item["series_type"],
            ObservationRecord.component == item["component"],
            ObservationRecord.obs_date == obs_date_parsed,
            ObservationRecord.superseded_by_id.is_(None),
        ).first()
        if dup is not None and dup.source != "generated":
            # 已有真实记录（导入/手工录入）→ 跳过，绝不覆盖
            skipped_dup += 1
            continue
        if dup is not None:
            # 覆盖系统自动生成的数据：旧记录先让位（status=superseded 释放唯一索引），
            # 待新记录插入后再回填 superseded_by_id 留痕（原始值永久保留、可追溯）。
            dup.status = "superseded"
            db.add(dup)
            db.flush()
        rec = ObservationRecord(
            point_id=point.id,
            series_type=item["series_type"],
            component=item["component"],
            obs_date=obs_date_parsed,
            value_mm=item["value_mm"],
            status="missing" if item["value_mm"] is None else "normal",
            source="excel",
            import_batch_id=batch.id,
            remark=item.get("remark"),
        )
        db.add(rec)
        db.flush()  # 取得新记录 id，供被覆盖的生成记录指向
        if dup is not None:
            dup.superseded_by_id = rec.id
            db.add(dup)
            replaced += 1
        imported += 1
    db.flush()
    batch.status = "confirmed"
    batch.imported_rows = imported
    batch.skipped_rows = (batch.skipped_rows or 0) + skipped_dup + skipped_no_date
    db.add(batch)
    db.commit()

    # 「把台账里的控制值同步到预警规则」—— 用户勾了才做（默认不勾，见前端）。
    # 台账里只有控制值这一对数字，报警值毫无依据 → 只动 control 级（见 sync_control_rules）。
    control_changes: list[dict] = []
    if sync_control_values:
        values = {
            ind: (v.get("control_cum_mm"), v.get("control_rate_mm_d"))
            for ind, v in ((batch.field_mapping or {}).get("control_values") or {}).items()
        }
        if values:
            from backend.services.warning_service import sync_control_rules

            _, control_changes = sync_control_rules(
                db, batch.project_id, values, note="自导入台账「控制值」同步"
            )

    return {
        "imported": imported,
        "replaced_generated": replaced,
        "skipped_dup": skipped_dup,
        "skipped_no_date": skipped_no_date,
        "control_changes": control_changes,
    }
