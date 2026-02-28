"""
配置路由 /api/config
"""
import json
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from database import get_config, set_config, db_fetchall

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


class BulkConfigRequest(BaseModel):
    configs: dict


@router.get("/all", summary="获取所有配置项")
async def get_all_config():
    rows = await db_fetchall("SELECT key, value, updated_at FROM app_config ORDER BY key")
    return {r["key"]: r["value"] for r in rows}


@router.post("/set", summary="设置单个配置项")
async def set_single(req: ConfigUpdateRequest):
    await set_config(req.key, req.value)
    return {"success": True, "key": req.key}


@router.post("/bulk", summary="批量设置配置项")
async def set_bulk(req: BulkConfigRequest):
    for key, value in req.configs.items():
        await set_config(key, str(value))
    return {"success": True, "updated": list(req.configs.keys())}


@router.get("/logs", summary="获取 API 请求日志")
async def get_logs(limit: int = 50):
    rows = await db_fetchall(
        "SELECT * FROM request_logs ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return {"logs": rows, "total": len(rows)}


@router.delete("/logs", summary="清空 API 日志")
async def clear_logs():
    from database import db_execute
    await db_execute("DELETE FROM request_logs")
    return {"success": True}
