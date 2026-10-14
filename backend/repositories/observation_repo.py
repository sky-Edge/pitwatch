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
