# -*- coding: utf-8 -*-
"""日报导出日志 —— 「今天这个项目出报了吗」的唯一依据。

用户 2026-11-16：想在项目列表上一眼看出**今天哪些项目做完了、哪些没做**，
而且「填数据 + 打印」两件都做完才算完成。

填数据可以从观测记录直接判断（当天有没有非生成的真实读数），
但「打印/导出」这个动作系统里原本没有任何留痕 —— 导完就下载走了。
所以这里记一条：谁、什么时候、导了哪几期、走的哪条路（Excel/PDF/Word/合并册）。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ExportLog(Base):
    __tablename__ = "export_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    #: 这次导出的期次（逗号分隔的 YYYY-MM-DD），多期批量时是一串
    obs_dates: Mapped[str] = mapped_column(String(2000), default="")
    #: excel / pdf / word / merged：用来区分"导出"和"打印"是不是都做了
    export_type: Mapped[str] = mapped_column(String(16), default="excel", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
