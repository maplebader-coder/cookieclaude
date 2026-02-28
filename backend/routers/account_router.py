"""
多账号管理路由 /api/accounts
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.account_service import AccountManager
from services.cookie_service import CookieManager, CookieRefreshScheduler
from services.claude_service import ClaudeAPIService
from database import get_config, set_config

router = APIRouter()


class CreateAccountRequest(BaseModel):
    name: str
    email: Optional[str] = ""
    org_uuid: Optional[str] = ""
    weight: Optional[int] = 1


class UpdateAccountRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    org_uuid: Optional[str] = None
    is_active: Optional[int] = None
    weight: Optional[int] = None


class CookieImportRequest(BaseModel):
    cookie_string: str
    account_id: int = 1


class PollStrategyRequest(BaseModel):
    strategy: str  # round_robin | weighted | least_fail


# ── 账号 CRUD ─────────────────────────────────────────────────

@router.get("/", summary="获取所有账号列表")
async def list_accounts():
    accounts = await AccountManager.list_accounts()
    return {"accounts": accounts, "total": len(accounts)}


@router.post("/", summary="创建新账号")
async def create_account(req: CreateAccountRequest):
    account_id = await AccountManager.create_account(
        name=req.name, email=req.email,
        org_uuid=req.org_uuid, weight=req.weight
    )
    return {"success": True, "account_id": account_id}


@router.patch("/{account_id}", summary="更新账号信息")
async def update_account(account_id: int, req: UpdateAccountRequest):
    updates = req.model_dump(exclude_none=True)
    success = await AccountManager.update_account(account_id, **updates)
    if not success:
        raise HTTPException(400, "无有效更新字段")
    return {"success": True}


@router.delete("/{account_id}", summary="删除账号")
async def delete_account(account_id: int):
    success = await AccountManager.delete_account(account_id)
    if not success:
        raise HTTPException(400, "账号 ID=1 为默认账号，不可删除")
    return {"success": True}


# ── Cookie 导入（按账号）─────────────────────────────────────

@router.post("/cookie/import", summary="为指定账号批量导入 Cookie")
async def import_cookies(req: CookieImportRequest):
    count = await CookieManager.parse_and_save_cookie_string(req.cookie_string, req.account_id)
    validation = await CookieManager.validate_required(req.account_id)
    # 自动探测并回写 org_uuid
    org_uuid = await ClaudeAPIService.get_org_uuid(req.account_id)
    return {
        "success": True,
        "account_id": req.account_id,
        "parsed_count": count,
        "validation": validation,
        "org_uuid_detected": org_uuid,
    }


# ── 连接测试 ─────────────────────────────────────────────────

@router.get("/{account_id}/test", summary="测试指定账号连接")
async def test_account(account_id: int):
    result = await ClaudeAPIService.check_auth(account_id)
    validation = await CookieManager.validate_required(account_id)
    return {**result, "validation": validation}


@router.get("/test-all", summary="测试所有账号连接")
async def test_all_accounts():
    accounts = await AccountManager.list_accounts()
    results = []
    for acc in accounts:
        result = await ClaudeAPIService.check_auth(acc["id"])
        results.append({
            "account_id": acc["id"],
            "name": acc["name"],
            **result,
        })
    return {"results": results}


# ── 轮询策略配置 ─────────────────────────────────────────────

@router.post("/poll-strategy", summary="设置账号轮询策略")
async def set_poll_strategy(req: PollStrategyRequest):
    allowed = {"round_robin", "weighted", "least_fail"}
    if req.strategy not in allowed:
        raise HTTPException(400, f"策略必须为: {allowed}")
    await set_config("account_poll_strategy", req.strategy)
    return {"success": True, "strategy": req.strategy}


@router.get("/poll-strategy", summary="获取当前轮询策略")
async def get_poll_strategy():
    strategy = await get_config("account_poll_strategy", "round_robin")
    return {"strategy": strategy}


# ── Cookie 自动刷新 ───────────────────────────────────────────

@router.post("/refresh/start", summary="启动 Cookie 自动刷新调度器")
async def start_refresh():
    await set_config("cookie_auto_refresh", "true")
    await CookieRefreshScheduler.start()
    return {"success": True, "message": "自动刷新已启动"}


@router.post("/refresh/stop", summary="停止 Cookie 自动刷新调度器")
async def stop_refresh():
    await set_config("cookie_auto_refresh", "false")
    await CookieRefreshScheduler.stop()
    return {"success": True, "message": "自动刷新已停止"}


@router.get("/refresh/logs", summary="获取 Cookie 刷新日志")
async def get_refresh_logs(limit: int = 50):
    from database import db_fetchall
    rows = await db_fetchall(
        "SELECT * FROM cookie_refresh_logs ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return {"logs": rows}


@router.post("/refresh/{account_id}", summary="立即刷新指定账号 Cookie")
async def refresh_account_now(account_id: int):
    result = await CookieRefreshScheduler.refresh_account(account_id)
    return result


# ── 模型管理 ─────────────────────────────────────────────────

@router.get("/models", summary="获取模型列表")
async def list_models():
    models = await ClaudeAPIService.list_models()
    return {"models": models}


@router.post("/models/{model_id}/toggle", summary="启用/禁用模型")
async def toggle_model(model_id: str, active: int = 1):
    from database import db_execute
    await db_execute(
        "UPDATE models SET is_active=? WHERE model_id=?", (active, model_id)
    )
    return {"success": True, "model_id": model_id, "is_active": active}
