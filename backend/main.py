# -*- coding: utf-8 -*-
"""FastAPI 应用入口。

启动时自动建表（V1 create_all，后续升级 Alembic 迁移）。
"""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from backend.api.v1 import audit, backup, baselines, batch_import, bulk, calculation, chart, comparison, diagnostics, export_templates, generate, holidays, imports, points, projects, records, reports, settings as app_settings_api, warning_events, warning_rules
from backend.core import reqlog
from backend.core.config import frontend_dist_dir, settings
from backend.core.db import harden_console, init_db
from backend.core.version import __version__ as APP_VERSION
from backend.services.export_template_service import seed_builtin_templates

# 控制台可能是 GBK：先把"编不出的字符写成 ? "这件事定下来，
# 否则迁移里一句带 ↔ / ⚠ 的 print 就能让整个后端起不来（2026-10-09 真实事故）。
# ⚠ 必须在 init_db() 之前执行 —— 所以放在模块导入期，而不是 lifespan 里。
harden_console()


def _seed_demo_if_empty() -> None:
    """演示模式：库是空的就自动建一个示例项目。

    为什么需要（2026-10-17）：Zeabur / Hugging Face 这类免费平台的容器磁盘是**临时的**，
    重启会把 database/ 丢掉。演示站不能因此变成空壳 —— 启动时发现一个项目都没有，
    就自己长出演示数据，别人随时点开都能看到完整流程（曲线、预警、导出日报）。
    真实工程数据留在用户本机，不进容器。

    只在 `MONITOR_DEMO_SEED=1` 时生效。判据是「库是空的」而不是「每次启动都建」——
    挂了持久卷的部署重启后项目还在，就不会多长出重复的示例项目。
    """
    if os.environ.get("MONITOR_DEMO_SEED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return

    from backend.core.db import SessionLocal
    from backend.services import project_service, sample_project_service

    try:
        with SessionLocal() as s:
            if project_service.list_projects(s, include_inactive=True):
                return  # 已经有数据（持久卷），别再长一个
            info = sample_project_service.create_sample_project(s, periods=6, point_count=8)
            s.commit()
        print(
            f"[演示模式] 空库，已自动创建示例项目：{info.get('project_name')} "
            f"（{info.get('points_created')} 个测点 / {info.get('records_generated')} 条演示数据）"
        )
    except Exception as exc:  # noqa: BLE001
        # 演示数据建不出来**不能**把服务带崩 —— 用户至少还能打开界面自己建项目
        print(f"[演示模式] 示例项目创建失败（不影响启动）：{exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # 补齐内置导出模板（幂等）
    from backend.core.db import SessionLocal

    with SessionLocal() as s:
        seed_builtin_templates(s)
    _seed_demo_if_empty()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # V1 本机/内网使用；上线前应收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 压缩：前端打包产物 2.69MB，经公网裸传要好几秒。
# gzip 之后约 800KB（JSON 接口同样受益）。minimum_size 大于 1KB 才压，
# 小响应压了反而多几个字节的头。
app.add_middleware(GZipMiddleware, minimum_size=1024)


#: 只读模式下仍然放行的路径 —— 解锁的入口本身必须能用，否则锁上就解不开
_READONLY_ALLOWED_PREFIXES = ("/api/v1/settings", "/api/health", "/docs", "/openapi.json")


@app.middleware("http")
async def _readonly_guard(request: Request, call_next):
    """只读锁定：打开后拒绝一切写操作。

    用户 2026-10-20：「还得加点防呆，感觉有些呆逼会玩坏」。

    为什么不是再叠几个确认框：确认框只拦得住手慢的，点多了就变成肌肉记忆，
    照样点。真正的防呆是**改不了** —— 打开锁定后，后端对任何写请求都回 403，
    对方怎么点都不动数据，界面还会明确告诉他"现在是只读"。

    注意两点：
    · `/api/v1/settings` 必须放行，否则锁定后连解锁都做不了，只能改库；
    · 读设置本身失败时按"未锁定"处理 —— 不能因为一次数据库抖动把整个系统锁死。
    """
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        path = request.url.path
        if not any(path.startswith(prefix) for prefix in _READONLY_ALLOWED_PREFIXES):
            from backend.core.db import SessionLocal
            from backend.services import app_settings_service

            try:
                with SessionLocal() as s:
                    locked = bool(app_settings_service.get_settings(s).get("readonly_mode"))
            except Exception:  # noqa: BLE001
                locked = False
            if locked:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "系统已锁定为「只读」，不能修改任何数据。"
                        "要解锁：回到主界面，关掉工具栏上的「只读锁定」。"
                    },
                )
    return await call_next(request)


@app.middleware("http")
async def _log_failed_requests(request: Request, call_next):
    """把 4xx/5xx 落盘（含完整 URL 与查询参数）。

    为什么值得单独加一层：uvicorn 的访问日志只在启动它的控制台窗口里，
    窗口一关就无从查证 —— 用户报「刚刚有个 400」时只能靠猜（真实发生过）。
    这里只记元信息、不碰响应体，且**记日志失败绝不影响请求本身**。
    """
    import time as _time

    t0 = _time.perf_counter()
    response = await call_next(request)
    if response.status_code >= 400:
        reqlog.log_failed_request(
            method=request.method,
            path=request.url.path,
            query=request.url.query or "",
            status=response.status_code,
            seconds=_time.perf_counter() - t0,
            client=request.client.host if request.client else None,
        )
    return response

app.include_router(projects.router, prefix=settings.api_prefix)
app.include_router(projects.brief_router, prefix=settings.api_prefix)
app.include_router(projects.attention_router, prefix=settings.api_prefix)
app.include_router(points.project_points_router, prefix=settings.api_prefix)
app.include_router(points.point_router, prefix=settings.api_prefix)
app.include_router(records.records_router, prefix=settings.api_prefix)
app.include_router(records.record_router, prefix=settings.api_prefix)
app.include_router(warning_rules.project_rules_router, prefix=settings.api_prefix)
app.include_router(warning_rules.sets_router, prefix=settings.api_prefix)
app.include_router(warning_rules.standards_router, prefix=settings.api_prefix)
app.include_router(calculation.computed_router, prefix=settings.api_prefix)
app.include_router(calculation.status_router, prefix=settings.api_prefix)
app.include_router(calculation.summary_router, prefix=settings.api_prefix)
app.include_router(comparison.comparison_router, prefix=settings.api_prefix)
app.include_router(baselines.baseline_router, prefix=settings.api_prefix)
app.include_router(baselines.dates_router, prefix=settings.api_prefix)
app.include_router(imports.import_upload_router, prefix=settings.api_prefix)
app.include_router(imports.batch_router, prefix=settings.api_prefix)
app.include_router(imports.multi_router, prefix=settings.api_prefix)
app.include_router(batch_import.batch_import_router, prefix=settings.api_prefix)
app.include_router(chart.chart_router, prefix=settings.api_prefix)
app.include_router(diagnostics.diag_router, prefix=settings.api_prefix)
app.include_router(warning_events.events_router, prefix=settings.api_prefix)
app.include_router(warning_events.quality_router, prefix=settings.api_prefix)
app.include_router(reports.reports_router, prefix=settings.api_prefix)
app.include_router(backup.backup_router, prefix=settings.api_prefix)
app.include_router(generate.gen_router, prefix=settings.api_prefix)
app.include_router(export_templates.tpl_router, prefix=settings.api_prefix)
app.include_router(export_templates.upload_router, prefix=settings.api_prefix)
app.include_router(holidays.holiday_router, prefix=settings.api_prefix)
# 主界面「项目默认值」+ 项目列表上的批量操作（用户 2026-10-11：少配一遍）
app.include_router(app_settings_api.settings_router, prefix=settings.api_prefix)
app.include_router(bulk.bulk_router, prefix=settings.api_prefix)
# 导出文件检查（用户 2026-10-12：检查我导出的日报）
app.include_router(audit.audit_router, prefix=settings.api_prefix)


# 后端能力清单 —— 前端用它判断"正在跑的后端是不是最新代码"。
#
# 【为什么需要】前端 vite 会热更新，后端 uvicorn 不会：改了 Python 必须重启。
# 于是会出现"界面是新的、行为是旧的"——用户点了新按钮，后端照旧逻辑执行，
# 表现就是"提示变了但数据没动"，极难自查。让后端自己报出有哪几项能力，
# 前端一比对就能明说"后端服务还是旧版本，请重启系统"。
BACKEND_FEATURES = [
    "generate_preview",      # 生成前冲突预检
    "generate_on_conflict",  # 遇到已有数据可选 跳过/替换
    "import_recheck",        # 导入改观测日期后重新核对
    "batch_projects",        # 一张清单批量建项目
    "holiday_service",       # 节假日安排（联网 + 缓存 + 内置保底）
    "generate_realistic_v4",     # 生成数据：无确定性趋势，围绕初值涨跌交替（分布对齐真实台账）
    "generate_total_station",    # 生成数据可加"全站仪观测模式"（每次架站的公共误差）
    "record_dates_with_values",  # 日期接口区分"有读数"与"仅缺测记录"（导出页据此标记可导出期次）
    "warning_fix_values",    # 一键修正报警值（把触发预警的读数更正回限值以内，原值保留）
    "export_expand_points",  # 母版点位少于项目测点时自动补行（不再静默丢掉多出来的点）
    "export_expand_raw_points",  # 原始数据区（X/Y/Z 坐标小表）也自动补块
    "export_no_warning",         # 导出报表不显示预警（预警等级列不填 + 清掉母版统计表的旧预警）
    "import_sheet_kind",         # 导入预览带"这张表是台账还是点位清单"，两个上传入口互相指路
    # 批量建项目只收点位清单 / 只填初始值的 V0 台账；逐期台账会被挡下并指路到「Excel 导入」
    "batch_projects_reject_ledger",
    # 上传母版时名称可留空（用文件名兜底）+ 支持事后重命名（旧后端会存成空名字）
    "template_name_optional",
    # 报表「控制值」两列与预警规则同源（旧后端：母版套打照抄母版、导入的控制值也不进规则）
    "control_values_single_source",
    # 导出时把母版里"别的基坑名"整体换成本项目名（旧后端会原样留着，报表串名）
    "master_project_name_sync",
    # 模板独立成"模板库"：由模板声明作用于哪些项目（多对多 + 通用），
    # 旧后端一个模板只属于一个项目，只能每个项目各传一遍
    "template_library",
    # 概况/分析/成果提交三页按本期数据自动生成（旧后端原样保留母版里**别的工程**的整段文字）
    "auto_text_pages",
    # 预警规则独立成"规则库"：一套规则声明作用于哪些项目（旧后端一个项目一套，8 个项目各抄一遍）
    "warning_rule_library",
    # 主界面集中管理：全站默认值（新建项目自动套规则/母版/天气）+ 项目列表批量操作
    # （旧后端没有 /settings，也没有 /bulk/projects/*，界面上那些按钮点了会 404）
    "app_settings",
    "bulk_project_ops",
    # 导出文件检查：扫一个文件夹，把导出的日报和系统逐项比（旧后端没有 /audit/*）
    "export_file_audit",
    # 母版体检：导出前把"这份母版会印出什么毛病"提前说出来（旧后端没有 /export-templates/{id}/check）
    "master_template_check",
    # 导出 Excel 预览：导出前先看整卷长什么样（旧后端没有 /reports/preview）
    "export_preview",
    # 跨项目导入：一次上传多份台账、按表内工程名称自动分到各项目（旧后端没有 /import/multi/*）
    "multi_project_import",
]


@app.get("/api/health", tags=["health"])
def health() -> dict:
    # version 给界面显示用（右上角），也是"我到底跑的是哪一版"的判据。
    # 唯一出处 backend/core/version.py —— 别在这儿另写一个字符串。
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": APP_VERSION,
        "features": BACKEND_FEATURES,
    }


# ---- 前端静态页（仅打包/生产形态启用）--------------------------------------
# 开发时前端由 vite dev server(5173) 提供；打包版把前端构建产物一并带上，
# 由后端在**同一个端口**发出去 —— 朋友那台电脑因此不需要装 Node，
# 也只有一个地址要记（http://127.0.0.1:8000）。
#
# ⚠ 必须挂在所有 API 路由**之后**：mount("/") 会吃掉所有未匹配路径，
#   放前面会把 /api/* 全部拦掉。
_static_dir = frontend_dist_dir()
if _static_dir is not None:
    from starlette.responses import Response
    from starlette.staticfiles import StaticFiles
    from starlette.types import Scope

    class CachedStaticFiles(StaticFiles):
        """带缓存的静态文件。

        【为什么要自己写】
        2026-10-29 用户反馈「打开网站很慢」，实测悉尼节点下行只有 **17 KB/s**，
        前端主包 869 KB 要传 50 多秒。而 `StaticFiles` 默认**不发 Cache-Control**，
        浏览器每次打开都要重新验证/下载 —— 慢上加慢。

        【缓存策略】
        · `assets/*`：Vite 产物文件名带内容 hash（`index-DTWSWfWM.js`），
          改内容必改名，所以可以 `immutable` 缓存一年，浏览器**连请求都不发**。
        · `index.html`：绝对不能缓存 —— 否则用户永远拿不到新版本的前端。
        """

        async def get_response(self, path: str, scope: Scope) -> Response:
            response = await super().get_response(path, scope)
            # ⚠ 用 **Content-Type** 判断 HTML，不看 path：
            # `StaticFiles(html=True)` 把 `/` 交给 index.html 时，传进来的 path 是空串，
            # 用 path 判断会漏掉首页 → 首页被缓存一小时 → 部署新版本后用户拿旧 index.html、
            # 再去请求已被删掉的旧 JS，**整页白屏**。（第一版就是这么写的，实测发现。）
            ctype = response.headers.get("content-type", "")
            if path.startswith("assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif "text/html" in ctype:
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            else:
                response.headers["Cache-Control"] = "public, max-age=3600"
            return response

    app.mount("/", CachedStaticFiles(directory=str(_static_dir), html=True), name="frontend")
