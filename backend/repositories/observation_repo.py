# -*- coding: utf-8 -*-
"""观测记录数据访问（repository）。

原始数据表只增不改：本层不提供物理删除；update 仅改 remark/status。
更正 = 生成新记录 + 旧记录 superseded_by_id 指向新记录（原值保留）。
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.observation_record import ObservationRecord
from backend.schemas.observation_record import RecordCreate, RecordUpdate

# 「已让位」的状态：既不占唯一索引，也不算生效记录。
#
# ⚠ **这是判据的唯一出处** —— `list_by_point` / `get_active_by_key` /
#   数据生成器（`_counts_as_occupied`）全都依据它。
#
# 曾经 `active_only` 只挡了 `superseded`、漏了 `void`，一次漏判连出三个问题：
#   ① 作废记录参与计算、日报与 Excel 导出；
#   ② 同一天同分量有两条时，前端 `find()` 取到作废那条 → **输入框空白**，
#      而状态列又因存在生效记录显示"已录入"，界面自相矛盾（用户实测报障）；
#   ③ 补录被误判成"已有生效记录，禁止重复录入"，用户改不了数据。
INACTIVE_STATUS = ("void", "superseded")


def get(db: Session, record_id: int) -> ObservationRecord | None:
    return db.get(ObservationRecord, record_id)


def get_active_by_key(
    db: Session, point_id: int, series_type: str, component: str, obs_date: date
) -> ObservationRecord | None:
    """查当日生效记录（既未被更正替代，也未作废）。防重检测与更正判断都基于它。"""
    return db.scalar(
        select(ObservationRecord).where(
            ObservationRecord.point_id == point_id,
            ObservationRecord.series_type == series_type,
            ObservationRecord.component == component,
            ObservationRecord.obs_date == obs_date,
            ObservationRecord.superseded_by_id.is_(None),
            ObservationRecord.status.notin_(INACTIVE_STATUS),
        )
    )


def list_by_project(
    db: Session,
    project_id: int,
    series_type: str | None = None,
    active_only: bool = True,
) -> list[ObservationRecord]:
    """整个项目的生效记录，按（测点、日期、分量）排好——**一次查询**。

    为什么要有它：预警汇总 / 项目结论 / 关注项 这类"整项目过一遍"的接口，
    原来是对每个测点各查一次（62 个点 × 若干分量 = 上百条 SQL），
    实测单次 `/warning-summary` 打了 1017 条查询、耗时 525ms。
    换成一次取回再按 point_id 分组，查询数从"测点数"降到 1。
    """
    from backend.models.point import MeasurementPoint

    stmt = (
        select(ObservationRecord)
        .join(MeasurementPoint, ObservationRecord.point_id == MeasurementPoint.id)
        .where(MeasurementPoint.project_id == project_id)
    )
    if active_only:
        stmt = stmt.where(
            ObservationRecord.superseded_by_id.is_(None),
            ObservationRecord.status.notin_(INACTIVE_STATUS),
        )
    if series_type:
        stmt = stmt.where(ObservationRecord.series_type == series_type)
    stmt = stmt.order_by(
        ObservationRecord.point_id,
        ObservationRecord.obs_date,
        ObservationRecord.series_type,
        ObservationRecord.component,
    )
    return list(db.scalars(stmt))


def group_by_point(records: list[ObservationRecord]) -> dict[int, list[ObservationRecord]]:
    """把 `list_by_project` 的结果按测点 id 分组（保持原有排序）。"""
    out: dict[int, list[ObservationRecord]] = {}
    for r in records:
        out.setdefault(r.point_id, []).append(r)
    return out



def distinct_dates(
    db: Session, project_id: int, with_values_only: bool = False
) -> list[date]:
    """项目有观测记录的日期（去重、升序）——**一条 SQL 解决**。

    报表/导出常要"这个项目都有哪几期"，以前是逐点取全部记录再在 Python 里做 set，
    50 个点就是 50 条查询 + 几千个对象。日期去重交给数据库做，几十微秒。
    """
    from backend.models.point import MeasurementPoint

    stmt = (
        select(ObservationRecord.obs_date)
        .join(MeasurementPoint, ObservationRecord.point_id == MeasurementPoint.id)
        .where(MeasurementPoint.project_id == project_id)
        .where(
            ObservationRecord.superseded_by_id.is_(None),
            ObservationRecord.status.notin_(INACTIVE_STATUS),
        )
    )
    if with_values_only:
        stmt = stmt.where(ObservationRecord.value_mm.is_not(None))
    return sorted({d for d in db.scalars(stmt).all() if d is not None})


def date_value_counts(db: Session, project_id: int) -> dict:
    """{日期: 该日有读数的条数} —— 用于判断"哪一期数据最完整"。"""
    from sqlalchemy import func

    from backend.models.point import MeasurementPoint

    stmt = (
        select(ObservationRecord.obs_date, func.count())
        .join(MeasurementPoint, ObservationRecord.point_id == MeasurementPoint.id)
        .where(MeasurementPoint.project_id == project_id)
        .where(
            ObservationRecord.superseded_by_id.is_(None),
            ObservationRecord.status.notin_(INACTIVE_STATUS),
            ObservationRecord.value_mm.is_not(None),
        )
        .group_by(ObservationRecord.obs_date)
    )
    return {d: int(c) for d, c in db.execute(stmt).all() if d is not None}


def list_by_point(
    db: Session,
    point_id: int,
    series_type: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    active_only: bool = True,
) -> list[ObservationRecord]:
    stmt = select(ObservationRecord).where(ObservationRecord.point_id == point_id)
    if active_only:
        # 同时排除「被更正」与「被作废」——只给生效记录。
        # 调用方（计算引擎 / 预警 / 日报 / 导出 / 对比）全都按这个语义使用它，
        # 少排一类就会让作废数据混进计算与报表。
        stmt = stmt.where(
            ObservationRecord.superseded_by_id.is_(None),
            ObservationRecord.status.notin_(INACTIVE_STATUS),
        )
    if series_type:
        stmt = stmt.where(ObservationRecord.series_type == series_type)
    if from_date:
        stmt = stmt.where(ObservationRecord.obs_date >= from_date)
    if to_date:
        stmt = stmt.where(ObservationRecord.obs_date <= to_date)
    stmt = stmt.order_by(ObservationRecord.obs_date, ObservationRecord.series_type, ObservationRecord.component)
    return list(db.scalars(stmt))


def create(db: Session, point_id: int, data: RecordCreate, source: str = "manual") -> ObservationRecord:
    rec = ObservationRecord(
        point_id=point_id,
        series_type=data.series_type,
        component=data.component,
        obs_date=data.obs_date,
        value_mm=data.value_mm,
        status="missing" if data.value_mm is None else "normal",
        source=source,
        remark=data.remark,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def correct(db: Session, old: ObservationRecord, new_value_mm: float | None, remark: str | None) -> ObservationRecord:
    """更正：旧记录让位（status=superseded）→ 插入新记录 → 旧记录指向新记录。

    顺序关键：必须先让旧记录退出唯一索引（status=superseded），再插入新记录，
    否则同日同分量会撞 uq_obs_active。原记录数值永不修改。
    """
    old.status = "superseded"
    db.flush()

    new_rec = ObservationRecord(
        point_id=old.point_id,
        series_type=old.series_type,
        component=old.component,
        obs_date=old.obs_date,
        value_mm=new_value_mm,
        status="missing" if new_value_mm is None else "normal",
        source="correct",
        remark=remark,
    )
    db.add(new_rec)
    db.flush()  # 取得新记录 id
    old.superseded_by_id = new_rec.id
    db.commit()
    db.refresh(new_rec)
    return new_rec


def update(db: Session, rec: ObservationRecord, data: RecordUpdate) -> ObservationRecord:
    if data.remark is not None:
        rec.remark = data.remark
    if data.status is not None:
        rec.status = data.status
    db.commit()
    db.refresh(rec)
    return rec
