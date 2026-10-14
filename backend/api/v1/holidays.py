# -*- coding: utf-8 -*-
"""节假日安排接口。

- `GET  /holidays/{year}`          取某年安排（缓存 → 联网 → 内置）
- `POST /holidays/{year}/refresh`  强制联网刷新

**设计要点：这两个接口永远返回 200**。拿不到数据时返回 `has_data=false` / `source=none`，
由前端退回内置数据 —— 现场没网（地下车库、工地）时，页面不该因为一个装饰性的
日历标记而报错。
"""
from fastapi import APIRouter, HTTPException

from backend.services import holiday_service

holiday_router = APIRouter(prefix="/holidays", tags=["holidays"])

# 允许的年份范围：防手滑请求 `/holidays/99999` 去打第三方接口
MIN_YEAR = 2000
MAX_YEAR = 2100


@holiday_router.get("/{year}")
def get_holidays(year: int) -> dict:
    """某年的法定节假日与调休上班日。"""
    _check_year(year)
    return holiday_service.get_holidays(year).to_dict()


@holiday_router.post("/{year}/refresh")
def refresh_holidays(year: int) -> dict:
    """强制联网刷新（用于"安排刚公布，我想立刻拿到"）。"""
    _check_year(year)
    return holiday_service.get_holidays(year, force_network=True).to_dict()


def _check_year(year: int) -> None:
    if not (MIN_YEAR <= year <= MAX_YEAR):
        raise HTTPException(status_code=400, detail=f"年份需在 {MIN_YEAR}~{MAX_YEAR} 之间")
