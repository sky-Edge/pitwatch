# -*- coding: utf-8 -*-
"""示例项目服务（提升首次使用体验）。

目的：新用户第一次打开系统面对空列表，要先建项目 → 建测点 → 配规则 → 录数据
才能看到第一张曲线，很容易在这条链路上流失。本服务把这条链路一次性跑完，
让用户点一下就能看到「有测点、有数据、有曲线、有日报」的完整形态。

组合的都是**已有能力**，不引入新的业务规则：
1. project_service.create_project        —— 建项目（带正负号规则）
2. warning_service.upsert_project_rules  —— 套用规范预设（默认八号基坑实测模板）
3. point_service.batch_create_points     —— 批量建测点
4. data_generator_service.generate_series—— 生成规范限值内的演示数据
5. warning_event_service.recompute_project —— 重算预警事件（派生表）

约束：
- 生成的观测数据一律 source=generated，与真实数据严格区分，可整批清理
  （POST /projects/{pid}/generate-data/cleanup）。
- 编号冲突时自动追加数字后缀，避免多次点击报错。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.repositories import project_repo
from backend.schemas.point import BatchPointItem
from backend.schemas.project import ProjectCreate
from backend.schemas.warning_rule import WarningRuleBatchWrite
from backend.services import data_generator_service, point_service, project_service, standards, warning_event_service, warning_service

# 默认示例参数：8 个测点、观测 3 期（第 0 期初值 + 第 1/2 期）
DEFAULT_POINT_PREFIX = "JJC"
DEFAULT_POINT_COUNT = 8
DEFAULT_PERIODS = 3
DEFAULT_PRESET_KEY = "custom_8hao"

# 生成的数据不触发报警，但偶发缺测是真实场景的一部分
DEFAULT_MISSING_RATE = 0.0

# 示例测点初始值（mm）：模拟真实读数量级，各测点错开 10mm 便于区分
DEFAULT_SETTLEMENT_BASE_MM = 4_200_000.0


def _unique_code(db: Session, base: str) -> str:
    """编号冲突时追加 -2 / -3 …，保证重复点击也能成功。"""
    if project_repo.get_by_code(db, base) is None:
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        if project_repo.get_by_code(db, candidate) is None:
            return candidate
    return f"{base}-{date.today().strftime('%m%d%H%M%S')}"


def create_sample_project(
    db: Session,
    *,
    start_date: date | None = None,
    periods: int = DEFAULT_PERIODS,
    preset_key: str = DEFAULT_PRESET_KEY,
    point_count: int = DEFAULT_POINT_COUNT,
    point_prefix: str = DEFAULT_POINT_PREFIX,
    missing_rate: float = DEFAULT_MISSING_RATE,
) -> dict:
    """一键创建一个可直接查看的示例项目。

    返回创建结果摘要（含项目 id、测点数、生成数据条数），供前端提示与跳转使用。
    """
    periods = max(2, min(int(periods), 30))  # 至少 2 期才有「本次变化」
    point_count = max(1, min(int(point_count), 50))
    end_date = start_date or date.today()
    begin_date = end_date - timedelta(days=periods - 1)

    # ① 建项目
    code = _unique_code(db, f"DEMO-{end_date.strftime('%m%d')}")
    project = project_service.create_project(
        db,
        ProjectCreate(
            code=code,
            name=f"示例项目（{point_count} 个测点 · {periods} 期演示数据）",
            location="系统自动创建，用于快速体验",
            sign_config={
                "settlement": {"positive_means": "down"},
                "displacement": {"positive_means": "toward_pit"},
            },
        ),
    )

    # ② 套用规范预设（默认八号基坑实测模板；预设不存在则退回 GB 50497 二级）
    preset = standards.get_preset(preset_key) or standards.get_preset("gb50497_l2")
    if preset is not None:
        warning_service.upsert_project_rules(
            db,
            project.id,
            WarningRuleBatchWrite(
                rules=preset.rules,
                source_note=f"示例项目自动套用：{preset.name}",
            ),
        )

    # ③ 批量建测点（沉降 + 水平位移 X/Y 都要，point_type=both）
    #
    # 关键：必须同时给 initial_value_mm。
    # data_generator_service 无 V0 时会跳过该分量（无历史读数无法可靠地滚动生成），
    # 而新建测点默认没有初始值 → 若不在这里给，生成器会一条数据都产出不了。
    # 取值拟合成工程习惯：沉降高程约 3.5~4.5m，位移坐标约 1000m 量级，单位 mm。
    items = [
        BatchPointItem(
            point_no=f"{point_prefix}{i}",
            point_type="both",
            initial_value_mm=DEFAULT_SETTLEMENT_BASE_MM + i * 10.0,
            location_desc=f"示例测点 {i}",
        )
        for i in range(1, point_count + 1)
    ]
    created = point_service.batch_create_points(db, project.id, items)

    # ④ 生成演示数据（无 V0 会被跳过，故首期值在此处写入并自动成为 V0）
    gen = data_generator_service.generate_series(
        db,
        project.id,
        begin_date,
        end_date,
        seed=20260915,  # 固定种子：同一示例每次结果一致，便于截图与教学
        missing_rate=missing_rate,
    )

    # ⑤ 重算预警事件（派生表，供列表页预警标签使用）
    recompute = warning_event_service.recompute_project(db, project.id)

    return {
        "project_id": project.id,
        "project_code": project.code,
        "project_name": project.name,
        "preset": preset.name if preset else None,
        "points_created": created.get("created", 0),
        "records_generated": gen.get("generated", 0),
        "start_date": begin_date.isoformat(),
        "end_date": end_date.isoformat(),
        "periods": periods,
        "warning_events": recompute.get("events_generated", 0),
        "note": (
            "示例数据 source=generated，与真实数据严格区分；"
            "如需清空可在“观测数据录入”页使用清理功能，或直接删除该项目。"
        ),
    }
