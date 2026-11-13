# -*- coding: utf-8 -*-
"""全站设置 API —— 主界面「项目默认值」用。

- GET /settings   读取（缺的键用默认值补齐）
- PUT /settings   修改（只改传进来的键）

用户 2026-10-11：「能不能把一些操作集中主界面上？每个项目都要调单独设置有点繁琐」
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.db import get_db
from backend.schemas.app_setting import AppSettingsRead, AppSettingsUpdate
from backend.services import app_settings_service

settings_router = APIRouter(prefix="/settings", tags=["settings"])


@settings_router.get("", response_model=AppSettingsRead)
def read_settings(db: Session = Depends(get_db)):
    """全站设置。"""
    return AppSettingsRead(**app_settings_service.get_settings(db))


@settings_router.put("", response_model=AppSettingsRead)
def write_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db)):
    """改设置。只改传进来的键 —— 没传的保持原样。

    ⚠ 这里**只影响以后新建的项目**，不会去动已经存在的项目（改历史配置是危险动作，
    要改请用项目列表里的「批量套用规则/母版」，那条路会明确告诉你影响了哪几个项目）。
    """
    data = payload.model_dump(exclude_unset=True)
    return AppSettingsRead(**app_settings_service.update_settings(db, data))


@settings_router.get("/instrument-presets")
def read_instrument_presets():
    """常见测量仪器备选库（只读）。

    用户 2026-11-13：「你把市面上的仪器都放在备选库了吧」—— 之前只能手打型号，
    打错一个字（莱卡 / Leica、DNA-03 / DNA03）验收时对不上。这里给出下拉预填。

    只返回厂牌 + 型号：精度、量程这类参数不同批次不一样，写死在库里会误导；
    验收看的是检定证书，不是这里的字。
    """
    from backend.services import instrument_service

    return instrument_service.list_presets()


# ─────────────────────── 电子签名 ───────────────────────


@settings_router.get("/signatures")
def list_signatures():
    """六个签字角色各自有没有签名、长什么样。

    用户 2026-11-13：「把这些签字栏都单独拉出来，我来自己放 png 签名」——
    一张图盖 11 处，等于让一个人同时签编制/审核/校核，资料上说不通。
    """
    from backend.services import signature_service

    return {"roles": signature_service.list_roles()}


@settings_router.post("/signatures/{role}")
async def upload_role_signature(role: str, file: UploadFile = File(...)):
    """给某个签字角色上传专属签名（编制/审核/现场监测人…）。"""
    from backend.services import signature_service

    if signature_service.get_role(role) is None:
        raise HTTPException(status_code=404, detail=f"没有这个签字栏：{role}")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="文件是空的")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="签名图片不要超过 8 MB")
    try:
        signature_service.save_role_signature(role, data)
    except signature_service.SignatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"role": role, "exists": True}


@settings_router.delete("/signatures/{role}")
def delete_role_signature(role: str):
    """删掉某个角色的签名 —— 该栏之后留空。"""
    from backend.services import signature_service

    if signature_service.get_role(role) is None:
        raise HTTPException(status_code=404, detail=f"没有这个签字栏：{role}")
    signature_service.delete_signature(role)
    return {"role": role, "exists": False}


@settings_router.get("/signature")
def get_signature():
    """旧的通用签名（一张图盖所有栏）。新界面用 /signatures，这里保留兼容。"""
    from backend.services import signature_service

    if not signature_service.has_signature():
        return {"exists": False, "image": None}
    import base64

    path = settings.signature_dir / "default.png"  # type: ignore[attr-defined]
    return {
        "exists": True,
        "image": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode(),
    }


@settings_router.post("/signature")
async def upload_signature(file: UploadFile = File(...)):
    """上传电子签名（PNG/JPG）。会转成 PNG 并裁掉四周白边。

    用户 2026-10-13：「给日报里加个电子签名，每天都签字很烦人」。
    上传一次，以后导出的日报会自动盖在「现场监测人：」等签字栏右边。
    """
    from backend.services import signature_service

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="文件是空的")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="签名图片不要超过 8 MB")
    try:
        signature_service.save_signature(data)
    except signature_service.SignatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"exists": True}


@settings_router.delete("/signature")
def delete_signature():
    """删掉电子签名 —— 之后的日报不再自动签名。"""
    from backend.services import signature_service

    signature_service.delete_signature()
    return {"exists": False}
