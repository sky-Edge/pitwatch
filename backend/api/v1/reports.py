# -*- coding: utf-8 -*-
"""报表导出 API —— Excel 日报 / Word 日报下载。

- GET /projects/{pid}/reports/excel?obs_date=&template_id=   按模板版式生成单期日报 Excel
- GET /projects/{pid}/reports/preview?obs_date=&template_id=  同一份 Excel 的界面预览（JSON 网格，不下载）
- GET /projects/{pid}/reports/excel/batch?obs_dates=&template_id=  多日期批量导出（ZIP 打包）
- GET /projects/{pid}/reports/daily?obs_date=               生成 Word 日报
template_id 缺省 → 内置标准模板；传项目模板 id → 以该模板原件为底模按其 layout 填数。
"""
import io
import zipfile
import json
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.reports import build_daily_docx, build_period_excel
from backend.services.project_service import get_project

reports_router = APIRouter(prefix="/projects/{project_id}/reports", tags=["reports"])

#: 导出时"补点/清行/文字生成"这几项摘要 —— 下载响应头和界面预览共用同一份清单
_SUMMARY_KEYS = (
    "template_points", "added_points", "shifted_rows",
    "missing_raw_points", "added_raw_points", "cleared_stats",
    "text_generated", "text_cleared", "text_overflow",
)


def _brief_summary(info: dict | None) -> dict:
    return {k: info[k] for k in _SUMMARY_KEYS if (info or {}).get(k)}


def _stream_response(buf, filename: str, info: dict | None = None) -> Response:
    """文件下载响应（中文文件名 URL 编码）。

    注意：不使用 StreamingResponse(BytesIO)——文件体已在内存中，
    而 Starlette 对 file-like 会按行（\\n）迭代，xlsx/zip 压缩字节含大量 0x0A，
    会被切成海量小分片逐个发送，下载慢 10 倍以上。用 Response 一次性发送。

    `info` 里的补点摘要放进 `X-Export-Summary` 头（URL 编码的 JSON）：
    母版只有 8 个点位、项目有 12 个测点时，导出文件会比母版多几行 ——
    界面得能说清"为什么多了几行 / 哪几个点母版根本放不下"，否则用户会以为导错了。
    """
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    brief = _brief_summary(info)
    if brief:
        headers["X-Export-Summary"] = quote(json.dumps(brief, ensure_ascii=False))
    buf.seek(0)
    return Response(content=buf.getvalue(), headers=headers)


def _build_excel_one(db, project_id: int, template_id: int | None, obs_date: date | None, weather: str | None):
    """按模板版式生成单期 Excel（单日接口与批量接口共用同一分支逻辑）。

    返回 (buf, filename, info)；模板原件缺失等业务错误抛 ValueError。
    """
    from backend.models.export_template import ExportTemplate
    from backend.reports.master_report import build_master_report
    from backend.reports.template_excel import build_from_template
    from backend.services import export_template_service

    if template_id:
        tpl = db.get(ExportTemplate, template_id)
        if tpl is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        if tpl.is_builtin:
            # 内置标准模板（无原件文件）→ 等价于未选模板，走系统标准版式
            return build_period_excel(db, project_id, obs_date)
        # 模板独立化之后模板有自己的"适用项目"清单 —— 用没绑本项目（且非通用）的母版导出，
        # 等于拿别的基坑的版式套本项目的数据，必须在源头拦下（此前这条校验是缺的）。
        if not export_template_service.is_usable_by(db, tpl, project_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"这份母版「{tpl.name}」没有绑定本项目 —— 请在导出页的母版下拉旁点"
                    "「设置适用项目」，把它加进来（或设为通用）"
                ),
            )
        layout = tpl.layout_json or {}
        if layout.get("sheets") and layout.get("meta"):
            return build_master_report(db, project_id, tpl, obs_date, weather=weather or "")
        # 旧式模板（仅数据表）→ 退回既有版式填充
        return build_from_template(db, project_id, tpl, obs_date)
    return build_period_excel(db, project_id, obs_date)


@reports_router.get("/excel")
def export_excel(
    project_id: int,
    obs_date: date | None = None,
    template_id: int | None = None,
    weather: str | None = None,
    db: Session = Depends(get_db),
):
    """导出整卷监测日报（单期）。

    - template_id 缺省 → 系统内置标准版式（竖向位移+水平位移两 Sheet）
    - template_id 指定且该模板已学习到完整母版布局（含封面/签字变量槽）→ 母版整卷模式：
      复制母版全部 Sheet，更新 期次/封面日期/签字日期/数据表时间/天气/数据区数值
    - weather：天气（晴/阴/雨…），写入数据表天气格
    """
    get_project(db, project_id)
    try:
        buf, filename, info = _build_excel_one(db, project_id, template_id, obs_date, weather)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _stream_response(buf, filename, info)


@reports_router.get("/preview")
def preview_excel(
    project_id: int,
    obs_date: date | None = None,
    template_id: int | None = None,
    weather: str | None = None,
    db: Session = Depends(get_db),
):
    """界面预览这份 Excel（不下载）：网格 JSON + 「控制值」逐格核对 + 补点摘要。

    与 `/excel` 走**同一个** `_build_excel_one` —— 预览所见即下载所得，
    不会出现"预览看着对、下载下来的文件另一回事"。生成的字节只在内存里，不落盘、不写库。

    控制值核对只在母版模式下做：内置标准版式每一行都直接取 `report_control_values`，
    结构上就对不上也没地方留旧值；而母版原件上那些导出够不着的格子才是风险点。
    """
    from backend.models.export_template import ExportTemplate
    from backend.reports.preview_model import bytes_to_preview, check_control_values
    from backend.services.warning_service import report_control_values

    get_project(db, project_id)
    try:
        buf, filename, info = _build_excel_one(db, project_id, template_id, obs_date, weather)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    raw = buf.getvalue()

    layout = None
    if template_id:
        tpl = db.get(ExportTemplate, template_id)  # 上面的分发已校验过存在性与适用范围
        if tpl is not None and not tpl.is_builtin:
            layout = tpl.layout_json
    checks = []
    if layout and (layout.get("sheets") and layout.get("meta")):
        rules = {k: report_control_values(db, project_id, k)
                 for k in ("settlement", "displacement")}
        checks = check_control_values(raw, layout, rules)

    return {"filename": filename, "summary": _brief_summary(info),
            "control_checks": checks, **bytes_to_preview(raw)}


@reports_router.get("/excel/batch")
def export_excel_batch(
    project_id: int,
    obs_dates: str = Query("", description="逗号分隔的观测日期 YYYY-MM-DD，如 2026-09-02,2026-09-08"),
    template_id: int | None = None,
    weather: str | None = None,
    db: Session = Depends(get_db),
):
    """批量导出多期日报：每个观测日期生成一份 Excel，打包为 ZIP 一次下载。

    - obs_dates 至少一个；单个日期生成失败不中断其余（压缩包内附《生成失败清单.txt》）
    - 全部失败 → 422 返回第一条错误
    """
    project = get_project(db, project_id)

    dates: list[date] = []
    for raw in [x.strip() for x in obs_dates.split(",") if x.strip()]:
        try:
            dates.append(date.fromisoformat(raw))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"无效观测日期：{raw}（应为 YYYY-MM-DD）") from None
    if not dates:
        raise HTTPException(status_code=422, detail="请至少选择一个观测日期（obs_dates 逗号分隔）")
    dates = sorted(set(dates))

    # 自定义模板原件缺失 / 没绑本项目 → 整批前置报错，避免逐期失败产生误导清单
    if template_id:
        from backend.models.export_template import ExportTemplate
        from backend.services import export_template_service

        tpl = db.get(ExportTemplate, template_id)
        if tpl is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        if not tpl.is_builtin and not export_template_service.is_usable_by(db, tpl, project_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"这份母版「{tpl.name}」没有绑定本项目 —— 请在导出页的母版下拉旁点"
                    "「设置适用项目」，把它加进来（或设为通用）"
                ),
            )
        if not tpl.is_builtin and not (tpl.file_path and Path(tpl.file_path).exists()):
            raise HTTPException(status_code=422, detail="模板原件文件缺失，请重新上传后再批量导出")

    zip_buf = io.BytesIO()
    errors: list[tuple[str, str]] = []
    # xlsx 内部已是 zip（XML 已压缩），外层再用 DEFLATED 压缩几乎无收益且空耗 CPU；
    # 用 ZIP_STORED 直接存储，批量导出可省下对 N×几 MB 的重复压缩时间。
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_STORED) as zf:
        for d in dates:
            try:
                buf, filename, _info = _build_excel_one(db, project_id, template_id, d, weather)
                zf.writestr(filename, buf.getvalue())
            except (ValueError, HTTPException) as e:
                errors.append((d.isoformat(), str(e.detail) if isinstance(e, HTTPException) else str(e)))
        if errors:
            lines = [f"{d}：{msg}" for d, msg in errors]
            zf.writestr("生成失败清单.txt", "\n".join(lines) + "\n")

    if not errors:
        n = len(dates)
        first, last = dates[0], dates[-1]
        filename = f"{project.name}监测日报_{first.isoformat()}~{last.isoformat()}_共{n}期.zip"
        return _stream_response(zip_buf, filename)

    if len(errors) == len(dates):
        raise HTTPException(status_code=422, detail=f"所选 {len(dates)} 个日期全部生成失败，原因：{errors[0][1]}")
    return _stream_response(zip_buf, f"{project.name}监测日报_部分失败_{len(errors)}期.zip")


@reports_router.get("/daily")
def export_daily_docx(project_id: int, obs_date: date | None = None, db: Session = Depends(get_db)):
    """导出 Word 观测日报。"""
    get_project(db, project_id)
    try:
        buf, filename, info = build_daily_docx(db, project_id, obs_date)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _stream_response(buf, filename)

