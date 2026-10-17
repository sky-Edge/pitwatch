# -*- coding: utf-8 -*-
"""导入引擎出入参 schema。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ImportPreviewRow(BaseModel):
    """预览单行（含校验结果）。"""

    point_no: str
    series_type: str
    component: str
    obs_date: str | None
    # 该行来自哪个数据 Sheet —— 用户按 Sheet 改观测日期时，前端要据此把
    # 新日期回填到该表的行上；不做这个字段，前端只能靠 date 猜，改期就会错乱。
    sheet_name: str | None = None
    value_mm: float | None
    initial_mm: float | None = None
    remark: str | None = None
    warning_level: str | None = None
    control_cum_mm: float | None = None
    control_rate_mm_d: float | None = None  # 控制值·变化速率（「控制值」合并组第二列）
    error: str | None = None
    error_kind: str | None = None  # unknown_point=测点不存在 / invalid=其他
    suggested: list[dict] = Field(default_factory=list)  # 未知点号的候选匹配
    existing_record_id: int | None = None
    existing_value_mm: float | None = None
    overridable: bool | None = None  # True=已有的是系统自动生成数据，导入将以用户数据覆盖
    needs_date: bool = False  # True=该行所属数据表没有观测日期，需用户指定后才能导入


class PointIssue(BaseModel):
    """未知点号聚合（需求②：初始点 Excel 导入）。"""

    point_no: str
    row_count: int
    series_types: list[str]
    components: list[str]
    infer_point_type: str  # settlement/displacement/both
    suggested: list[dict] = Field(default_factory=list)


class ControlValueSuggest(BaseModel):
    """台账里写着的「控制值」 vs 项目当前预警规则。

    报表上的「控制值」两列（累计变化量 / 变化速率）与预警规则的 control 级
    **必须是同一个数**（唯一出处 `warning_service.report_control_values`）。
    台账里读到值而项目没配规则时，这里给用户一个"导入时顺带同步"的机会。
    """

    indicator: str                      # settlement / displacement
    control_cum_mm: float | None = None       # 台账里写的累计控制值
    control_rate_mm_d: float | None = None    # 台账里写的速率控制值
    row_count: int = 0                  # 有控制值的行数
    matched_rows: int = 0               # 其中与"多数票"一致的行数
    current_cum_mm: float | None = None       # 项目当前规则（未配为 None）
    current_rate_mm_d: float | None = None
    differs: bool = False               # 两边不一致（含"还没配规则"）
    has_rule: bool = False              # 项目是否已配 control 规则


class ImportPreviewResponse(BaseModel):
    batch_id: int
    filename: str
    sheets: list[dict[str, Any]] = Field(default_factory=list)
    valid_rows: list[ImportPreviewRow] = Field(default_factory=list)
    override_rows: list[ImportPreviewRow] = Field(
        default_factory=list, description="与系统自动生成数据同日，导入后将以用户数据覆盖"
    )
    conflict_rows: list[ImportPreviewRow] = Field(default_factory=list)
    error_rows: list[ImportPreviewRow] = Field(default_factory=list)
    no_date_rows: list[ImportPreviewRow] = Field(
        default_factory=list, description="未识别观测日期的行（前端补填日期后可导入）"
    )
    point_issues: list[PointIssue] = Field(default_factory=list)
    control_suggest: list[ControlValueSuggest] = Field(
        default_factory=list, description="台账里的控制值 vs 当前预警规则（可不一致）"
    )


class PointResolveAction(BaseModel):
    """用户对未知点号的处理：二选一。

    - auto_create: 以 Excel 点号自动建点（type 按分量推断）
    - map_to:      映射到项目已有测点编号（重写该点号所有行）
    """

    excel_no: str
    auto_create: bool = False
    infer_point_type: str = "both"
    map_to: str | None = None


class SheetDateOverride(BaseModel):
    """用户为某个数据 Sheet 指定的观测日期。

    obs_date=None 表示"不指定/清空"（保持表里没日期，那些行不会入库）；
    指定后，该表所有读数都记为这一天。
    """

    sheet_name: str
    obs_date: str | None = None  # YYYY-MM-DD


class ImportRecheckRequest(BaseModel):
    """按用户指定的观测日期重新核对（不落库）。

    典型场景：表里识别到的日期不对，或本身没写日期（初始值清单），
    用户指定正确的「观测/初测日期」后，需要重新判定有效行/冲突行 ——
    换个日期，冲突情况会变（新日期可能已有记录，也可能把原冲突解开）。
    """

    dates: list[SheetDateOverride] = Field(default_factory=list)


class ImportConfirmRequest(BaseModel):
    """确认导入：前端回传用户确认后的有效行（可剔除不想导入的行）。"""

    valid_rows: list[ImportPreviewRow] = Field(default_factory=list, description="用户确认导入的行清单")
    point_actions: list[PointResolveAction] = Field(default_factory=list, description="未知点号的处理动作（建点/映射）")
    sync_control_values: bool = Field(
        default=False,
        description="把台账里的「控制值」同步进项目预警规则（只动 control 级；默认不动）",
    )


class ImportBatchRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    project_id: int
    filename: str
    sheet_name: str | None
    status: str
    total_rows: int
    valid_rows: int
    imported_rows: int
    skipped_rows: int
    conflict_rows: int
    error_summary: dict[str, Any]
    created_at: datetime


# ─────────────── 跨项目导入（一次上传、自动分到各项目）───────────────


class MultiImportSheet(BaseModel):
    """一张数据表 + 它认到了哪个项目（`project_id=None` = 没认出来，要用户手选）。"""

    sheet_name: str
    series_type: str = ""
    kind: str = "ledger"
    obs_date: str | None = None
    row_count: int = 0
    #: 表里读到的工程名称候选（界面上要能回答"你凭什么认成它"）
    name_texts: list[str] = Field(default_factory=list)
    project_id: int | None = None
    project_code: str = ""
    project_name: str = ""
    project_archived: bool = False
    how: str = ""
    score: float = 0.0
    #: 只是"相近"（不是完全一致）→ 提醒核一眼
    close: bool = False
    #: 表里没写名称、只按文件名认的 → 更要提醒核一眼
    from_filename: bool = False
    ambiguous: bool = False
    reason: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class MultiImportGroup(BaseModel):
    """认到同一个项目的一批表 = 一个批次（「全部导入」里的一项）。"""

    batch_id: int
    project_id: int
    project_code: str = ""
    project_name: str = ""
    project_archived: bool = False
    filename: str
    sheets: list[MultiImportSheet] = Field(default_factory=list)
    preview: ImportPreviewResponse
    #: 有哪张表是"按文件名认的"或只是"相近"→ 界面上提醒核一眼
    needs_eyeball: bool = False


class MultiImportPending(BaseModel):
    """认不出是哪个项目的表：**不建批次**，等用户手选后走 /multi/assign。

    `upload_name` 是暂存目录里的文件名（不是完整路径 —— 服务端路径不该发给前端），
    指派时后端只在暂存目录里找这个名字，找不到就报错，绝不按前端给的字符串拼路径。
    """

    upload_name: str
    filename: str
    sheet: MultiImportSheet


class MultiImportPointList(BaseModel):
    """点位清单（「点号 + 坐标/高程」）—— 那是「批量建项目」的活，这里只挑出来说明。"""

    filename: str
    sheet_name: str
    row_count: int = 0


class MultiImportUploadResponse(BaseModel):
    groups: list[MultiImportGroup] = Field(default_factory=list)
    pending: list[MultiImportPending] = Field(default_factory=list)
    point_lists: list[MultiImportPointList] = Field(default_factory=list)
    #: 解析失败的文件（一份坏了不该拖住其余的）—— 绝不静默少一个文件
    errors: list[str] = Field(default_factory=list)


class MultiImportAssignRequest(BaseModel):
    """把"认不出"的表指派给某个项目 → 建批次 + 出预览。"""

    upload_name: str
    sheet_names: list[str] = Field(default_factory=list)
    project_id: int
    #: 原始文件名（只为界面上好看；不传就用暂存名）
    filename: str | None = None

