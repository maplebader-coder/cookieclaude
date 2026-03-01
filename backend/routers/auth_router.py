"""
认证路由 /api/auth v2.1
修复：
  - CookieLoginRequest 补全所有 Cookie 字段
  - cookie_map 对应更新
  - 批量导入路由支持 TSV 格式解析
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional

from services.cookie_service import CookieManager, parse_any_cookie_text
from services.claude_service import ClaudeAPIService
from services.oauth_service import GoogleOAuthService

router = APIRouter()


class CookieLoginRequest(BaseModel):
    # ── 核心身份认证 ─────────────────────────────────────────
    sessionKey: str
    anthropic_device_id: Optional[str] = ""
    ssid: Optional[str] = ""                     # __ssid

    # ── Cloudflare ────────────────────────────────────────────
    cf_clearance: Optional[str] = ""
    cf_bm: Optional[str] = ""                    # __cf_bm
    cfuvid: Optional[str] = ""                   # _cfuvid / cfuvid

    # ── 路由 & 基础设施 ───────────────────────────────────────
    routing_hint: Optional[str] = ""             # routingHint
    last_active_org: Optional[str] = ""          # lastActiveOrg
    activity_session_id: Optional[str] = ""      # activitySessionId
    cookie_seed_done: Optional[str] = ""         # cookie_seed_done

    # ── 用户偏好 ──────────────────────────────────────────────
    color_scheme: Optional[str] = "light"        # CH-prefers-color-scheme
    user_sidebar_visible: Optional[str] = ""     # user-sidebar-visible-on-load
    user_sidebar_pinned: Optional[str] = ""      # user-sidebar-pinned
    consent_preferences: Optional[str] = ""      # anthropic-consent-preferences

    # ── 第三方追踪 ────────────────────────────────────────────
    ajs_anonymous_id: Optional[str] = ""
    ajs_user_id: Optional[str] = ""
    fbp: Optional[str] = ""                      # _fbp
    hubspotutk: Optional[str] = ""
    g_state: Optional[str] = ""                  # Google state
    dd_s: Optional[str] = ""                     # _dd_s

    # ── Intercom ──────────────────────────────────────────────
    intercom_session: Optional[str] = ""         # intercom-session-lupk8zyo
    intercom_device_id: Optional[str] = ""       # intercom-device-id-lupk8zyo

    # ── 原始字符串优先 ────────────────────────────────────────
    raw_cookie_string: Optional[str] = ""
    account_id: Optional[int] = 1


class CookieBatchRequest(BaseModel):
    cookie_string: str
    account_id: Optional[int] = 1


class OAuthConfigRequest(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: Optional[str] = "http://localhost:8765/api/auth/google/callback"


class BrowserLoginRequest(BaseModel):
    email: Optional[str] = ""
    password: Optional[str] = ""
    account_id: Optional[int] = 1


# ── Cookie 认证 ──────────────────────────────────────────────

@router.post("/cookie/login", summary="Cookie 直接注入登录（字段全量）")
async def cookie_login(req: CookieLoginRequest):
    if not req.sessionKey:
        raise HTTPException(400, "sessionKey 不能为空")

    account_id = req.account_id or 1

    # 优先使用原始字符串（TSV / 标准格式均支持）
    if req.raw_cookie_string and req.raw_cookie_string.strip():
        cookies, fmt = parse_any_cookie_text(req.raw_cookie_string)
        count = await CookieManager.save_batch(cookies, account_id)
        validation = await CookieManager.validate_required(account_id)
        return {
            "success": True,
            "method": f"raw_string ({fmt})",
            "imported": count,
            "validation": validation,
            "account_id": account_id,
        }

    # 字段逐一写入
    cookie_map = {
        "sessionKey":                   req.sessionKey,
        "anthropic-device-id":          req.anthropic_device_id,
        "__ssid":                       req.ssid,
        "cf_clearance":                 req.cf_clearance,
        "__cf_bm":                      req.cf_bm,
        "_cfuvid":                      req.cfuvid,
        "cfuvid":                       req.cfuvid,
        "routingHint":                  req.routing_hint,
        "lastActiveOrg":                req.last_active_org,
        "activitySessionId":            req.activity_session_id,
        "cookie_seed_done":             req.cookie_seed_done,
        "CH-prefers-color-scheme":      req.color_scheme,
        "user-sidebar-visible-on-load": req.user_sidebar_visible,
        "user-sidebar-pinned":          req.user_sidebar_pinned,
        "anthropic-consent-preferences": req.consent_preferences,
        "ajs_anonymous_id":             req.ajs_anonymous_id,
        "ajs_user_id":                  req.ajs_user_id,
        "_fbp":                         req.fbp,
        "hubspotutk":                   req.hubspotutk,
        "g_state":                      req.g_state,
        "_dd_s":                        req.dd_s,
        "intercom-session-lupk8zyo":    req.intercom_session,
        "intercom-device-id-lupk8zyo":  req.intercom_device_id,
    }

    count = await CookieManager.save_batch(
        {k: v for k, v in cookie_map.items() if v}, account_id
    )
    validation = await CookieManager.validate_required(account_id)
    return {
        "success": True,
        "method": "direct_inject",
        "saved_count": count,
        "validation": validation,
        "account_id": account_id,
    }


@router.post("/cookie/batch", summary="批量解析 Cookie 字符串（支持 DevTools TSV + 标准格式）")
async def cookie_batch(req: CookieBatchRequest):
    account_id = req.account_id or 1
    cookies, fmt = parse_any_cookie_text(req.cookie_string)
    count = await CookieManager.save_batch(cookies, account_id)
    validation = await CookieManager.validate_required(account_id)
    return {
        "success": True,
        "parsed_count": count,
        "format_detected": fmt,
        "validation": validation,
        "account_id": account_id,
    }


@router.delete("/cookie/clear", summary="清除指定账号所有 Cookie")
async def clear_cookies(account_id: int = 1):
    await CookieManager.clear_all(account_id)
    return {"success": True, "message": f"账号 {account_id} 的所有 Cookie 已清除"}


# ── 连接测试 ──────────────────────────────────────────────────

@router.get("/test", summary="测试 Claude.ai 连接（默认账号）")
async def test_connection(account_id: int = 1):
    validation = await CookieManager.validate_required(account_id)
    if not validation["session_key_set"]:
        return {"connected": False, "reason": "sessionKey 未配置", "validation": validation}
    result = await ClaudeAPIService.check_auth(account_id)
    return {"connected": result.get("authenticated", False), "data": result, "validation": validation}


@router.get("/status", summary="获取当前认证状态")
async def auth_status(account_id: int = 1):
    validation = await CookieManager.validate_required(account_id)
    oauth_status = await GoogleOAuthService.get_oauth_status()
    return {"cookie_auth": validation, "oauth": oauth_status, "account_id": account_id}


# ── Google OAuth ──────────────────────────────────────────────

@router.post("/google/config", summary="配置 Google OAuth 凭证")
async def set_google_config(req: OAuthConfigRequest):
    await GoogleOAuthService.save_config(req.client_id, req.client_secret, req.redirect_uri)
    return {"success": True, "message": "Google OAuth 配置已保存"}


@router.get("/google/config", summary="获取 Google OAuth 配置状态")
async def get_google_config():
    return await GoogleOAuthService.get_oauth_status()


@router.get("/google/authorize", summary="获取 Google OAuth 授权 URL")
async def google_authorize():
    result = await GoogleOAuthService.generate_auth_url()
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@router.get("/google/callback", summary="Google OAuth 回调处理", include_in_schema=False)
async def google_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return {"success": False, "error": error}
    if not code:
        raise HTTPException(400, "缺少 code 参数")
    result = await GoogleOAuthService.handle_callback(code, state)
    if result["success"]:
        return RedirectResponse(
            url=f"/#/auth?google_ok=1&email={result.get('email', '')}"
        )
    return result


# ── Playwright 浏览器自动登录 ─────────────────────────────────

@router.post("/browser/login", summary="浏览器自动化登录（捕获 Cookie）")
async def browser_login(req: BrowserLoginRequest):
    result = await GoogleOAuthService.browser_auto_login(
        email=req.email, password=req.password, account_id=req.account_id or 1
    )
    return result


@router.post("/proxy/import", summary="从 mitmproxy 导入捕获的 Cookie")
async def import_proxy_cookies(data: dict):
    cookies = data.get("cookies", data)
    account_id = data.get("account_id", 1)
    result = await GoogleOAuthService.capture_from_proxy_log(
        cookies if isinstance(cookies, str) else __import__("json").dumps(cookies),
        account_id=account_id,
    )
    return result


@router.get("/proxy/load-file", summary="从本地 cookies.json 文件加载")
async def load_proxy_file(path: str = "cookies.json", account_id: int = 1):
    result = await GoogleOAuthService.load_proxy_cookies_from_file(path, account_id=account_id)
    return result
