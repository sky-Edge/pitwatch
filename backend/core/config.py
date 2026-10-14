# -*- coding: utf-8 -*-
"""全局配置。

约定：
- 所有时间戳使用本地时间（naive datetime），数据库不存时区，避免漂移。
- 所有数值字段带单位后缀（value_mm / delta_mm / cumulative_mm / rate_mm_per_day）。
- 单位统一 mm；速率 mm/day。
"""
import os
import sys
from pathlib import Path


def _resolve_base_dir() -> Path:
    """数据根目录（数据库/模板/备份/日志都挂在它下面）。

    ★ 打包（PyInstaller）后**必须**用 exe 所在目录，不能用 `__file__`：
      · onedir 模式：`__file__` 落在 `_internal/` 里；
      · onefile 模式：落在 `%TEMP%/_MEIxxxx/`（每次启动都不一样、退出即删）。
      两种都会让"用户的数据库"下次启动就没了 —— 对监测系统是灾难级事故。
      exe 同级目录既是用户找得到的，也一定是可写的（换台电脑解压就能用）。
    """
    if getattr(sys, "frozen", False):  # PyInstaller 运行时为 True
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent  # 项目根目录


BASE_DIR = _resolve_base_dir()


def frontend_dist_dir() -> Path | None:
    """前端构建产物目录；不存在时返回 None。

    打包版靠它做**单端口运行**（后端顺便把页面发出去），这样朋友那台电脑
    连 Node 都不用装。开发时前端走 vite dev server(5173)，这里返回什么无所谓。
    """
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", BASE_DIR))
        candidate = root / "frontend_dist"
    else:
        candidate = BASE_DIR / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


class Settings:
    """应用配置。数据/备份/模板目录均可用环境变量覆盖（测试必须覆盖，见下）。"""

    app_name: str = "基坑拉森钢板桩沉降与水平位移观测系统"
    api_prefix: str = "/api/v1"

    # 数据库：默认 database/monitor.db，测试通过 MONITOR_DB_URL 覆盖
    database_dir: Path = BASE_DIR / "database"
    database_url: str = os.environ.get(
        "MONITOR_DB_URL", f"sqlite:///{database_dir / 'monitor.db'}"
    )

    # ⚠ 下面两个目录**必须**可被环境变量覆盖。
    # 原因（真实事故，2026-09-26 查明）：
    # 多个测试的清理 fixture 会删除 backup_dir 下的全部 *.db，
    # tests/test_hard_delete_template.py 还会 rmtree(templates_dir/<项目>/)。
    # 这两个目录原先硬编码指向生产目录，而 conftest 只隔离了数据库，
    # 于是每跑一次测试就删掉用户的真实备份与上传模板。
    # 实测损失：30 条备份记录中 29 个文件永久丢失，8 个上传的导出模板全部丢失。
    templates_dir: Path = Path(
        os.environ.get("MONITOR_TEMPLATES_DIR", str(BASE_DIR / "templates"))
    )
    backup_dir: Path = Path(
        os.environ.get("MONITOR_BACKUP_DIR", str(BASE_DIR / "database" / "backups"))
    )
    # Excel 导入的上传暂存目录。同样需要覆盖：测试会往里写文件，
    # 若指向生产目录就会把测试用的假台账混进真实上传目录（实测一次全量测试写进 19 个）。
    upload_dir: Path = Path(
        os.environ.get("MONITOR_UPLOAD_DIR", str(BASE_DIR / "database" / "uploads"))
    )

    # 节假日安排的联网缓存（每年一个小 JSON）。同样必须可覆盖：
    # 测试会写缓存文件，指向生产目录就会把测试的假数据混进去 —— 而它是**功能数据**，
    # 混进去之后日期面板会显示错误的放假安排，比丢个临时文件严重得多。
    holiday_cache_dir: Path = Path(
        os.environ.get(
            "MONITOR_HOLIDAY_CACHE_DIR", str(BASE_DIR / "database" / "cache" / "holidays")
        )
    )

    # 运行日志目录（后端请求错误 + 启动输出）。
    # 为什么要落盘：uvicorn 的访问日志默认只打到**启动它的那个控制台窗口**，
    # 窗口一关就没了 —— 用户报「刚刚有个 400」时根本无从查起（2026-10-05 实际发生）。
    # 同样必须可覆盖：测试会往里写，指向生产目录就会污染真实日志。
    log_dir: Path = Path(os.environ.get("MONITOR_LOG_DIR", str(BASE_DIR / "logs")))

    def ensure_dirs(self) -> None:
        self.database_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.holiday_cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
