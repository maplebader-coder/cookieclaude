"""
Cookie 管理路由 /api/cookie v2.0
支持 account_id 参数，实现多账号 Cookie 隔离查询
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from services.cookie_service import CookieManager, COOKIE_DEFINITIONS

router = APIRouter()


class SetCookieRequest(BaseModel):
    name: str
    value: str
    expires_at: Optional[str] = None
    account_id: Optional[int] = 1


@router.get("/all", summary="获取指定账号所有 Cookie（含元数据）")
async def get_all_cookies(account_id: int = 1):
    cookies = await CookieManager.get_all_with_meta(account_id)
    return {"cookies": cookies, "total": len(cookies), "account_id": account_id}


@router.get("/definitions", summary="获取 Cookie 字段定义文档")
async def get_definitions():
    return {"definitions": COOKIE_DEFINITIONS}


@router.post("/set", summary="设置单个 Cookie")
async def set_cookie(req: SetCookieRequest):
    await CookieManager.save_cookie(req.name, req.value, req.expires_at, req.account_id or 1)
    return {"success": True, "name": req.name, "account_id": req.account_id}


@router.delete("/{name}", summary="删除指定 Cookie")
async def delete_cookie(name: str, account_id: int = 1):
    await CookieManager.delete_cookie(name, account_id)
    return {"success": True}


@router.get("/validate", summary="验证必需 Cookie 是否完整")
async def validate_cookies(account_id: int = 1):
    return await CookieManager.validate_required(account_id)


@router.get("/header", summary="预览当前 Cookie 请求头（脱敏）")
async def preview_header(account_id: int = 1):
    header = await CookieManager.build_cookie_header(account_id)
    import re
    safe = re.sub(r"(sessionKey=sk-ant-)[^;]+", r"\1***[REDACTED]***", header)
    return {"cookie_header": safe, "length": len(header), "account_id": account_id}
