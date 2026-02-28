"""
Google OAuth 认证服务 v2.0
新增：account_id 参数，多账号独立 Cookie 隔离
"""
import httpx, json, secrets, hashlib, base64, sys, os
from urllib.parse import urlencode
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_config, set_config
from services.cookie_service import CookieManager

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO  = "https://www.googleapis.com/oauth2/v2/userinfo"

_oauth_states: dict = {}


class GoogleOAuthService:

    @staticmethod
    async def get_config() -> dict:
        return {
            "client_id":     await get_config("google_client_id", ""),
            "client_secret": await get_config("google_client_secret", ""),
            "redirect_uri":  await get_config("oauth_redirect_uri",
                                              "http://localhost:8765/api/auth/google/callback"),
        }

    @staticmethod
    async def save_config(client_id: str, client_secret: str, redirect_uri: str = ""):
        await set_config("google_client_id", client_id)
        await set_config("google_client_secret", client_secret)
        if redirect_uri:
            await set_config("oauth_redirect_uri", redirect_uri)

    @staticmethod
    async def is_configured() -> bool:
        cfg = await GoogleOAuthService.get_config()
        return bool(cfg["client_id"] and cfg["client_secret"])

    # ── 方案A：标准 OAuth 授权码 + PKCE ─────────────────────

    @staticmethod
    async def generate_auth_url() -> dict:
        cfg = await GoogleOAuthService.get_config()
        if not cfg["client_id"]:
            return {"success": False, "error": "未配置 Google client_id"}

        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        _oauth_states[state] = {
            "code_verifier": code_verifier,
            "created_at": datetime.utcnow().isoformat(),
        }

        params = {
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "select_account",
            "client_id": cfg["client_id"],
            "redirect_uri": cfg["redirect_uri"],
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return {
            "success": True,
            "auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}",
            "state": state,
        }

    @staticmethod
    async def handle_callback(code: str, state: str) -> dict:
        if state not in _oauth_states:
            return {"success": False, "error": "无效的 state，可能是 CSRF 攻击"}
        state_data = _oauth_states.pop(state)
        cfg = await GoogleOAuthService.get_config()
        token_data = {
            "code": code,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri": cfg["redirect_uri"],
            "grant_type": "authorization_code",
            "code_verifier": state_data["code_verifier"],
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(GOOGLE_TOKEN_URL, data=token_data)
                if token_resp.status_code != 200:
                    return {"success": False, "error": f"换取 token 失败: {token_resp.text}"}
                tokens = token_resp.json()
                access_token = tokens.get("access_token")
                userinfo_resp = await client.get(
                    GOOGLE_USERINFO,
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                userinfo = userinfo_resp.json()

            await set_config("google_access_token", access_token)
            await set_config("google_user_email", userinfo.get("email", ""))
            await set_config("google_user_name", userinfo.get("name", ""))

            return {
                "success": True,
                "email": userinfo.get("email"),
                "name": userinfo.get("name"),
                "picture": userinfo.get("picture"),
                "next_step": "Google 授权成功！请使用 Playwright 方案完成 claude.ai 会话建立，捕获 sessionKey",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 方案B：Playwright 浏览器自动化 ──────────────────────

    @staticmethod
    async def browser_auto_login(email: str = "", password: str = "",
                                 account_id: int = 1) -> dict:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "success": False,
                "error": "需要安装: pip install playwright && playwright install chromium",
            }
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False)
                context = await browser.new_context()
                page = await context.new_page()

                await page.goto("https://claude.ai/login")
                await page.wait_for_load_state("networkidle")

                try:
                    google_btn = page.locator("text=Continue with Google").first
                    await google_btn.click()
                    await page.wait_for_url("**/accounts.google.com/**", timeout=10000)
                except Exception:
                    return {"success": False, "error": "未找到 'Continue with Google' 按钮"}

                if email:
                    try:
                        await page.locator('input[type="email"]').fill(email)
                        await page.keyboard.press("Enter")
                        await page.wait_for_timeout(2000)
                        if password:
                            await page.locator('input[type="password"]').fill(password)
                            await page.keyboard.press("Enter")
                    except Exception:
                        pass

                try:
                    await page.wait_for_url("**/claude.ai/**", timeout=120000)
                    await page.wait_for_load_state("networkidle")
                except Exception:
                    await browser.close()
                    return {"success": False, "error": "登录超时"}

                raw_cookies = await context.cookies()
                cookie_dict = {
                    c["name"]: c["value"]
                    for c in raw_cookies
                    if "claude.ai" in c.get("domain", "")
                }
                await browser.close()

                if not cookie_dict.get("sessionKey"):
                    return {
                        "success": False,
                        "error": "未能捕获 sessionKey",
                        "captured": list(cookie_dict.keys()),
                    }

                count = await CookieManager.save_batch(cookie_dict, account_id)
                validation = await CookieManager.validate_required(account_id)
                return {
                    "success": True,
                    "message": f"账号 {account_id} 捕获 {count} 个 Cookie",
                    "captured_keys": list(cookie_dict.keys()),
                    "validation": validation,
                    "account_id": account_id,
                    "count": count,
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 方案C：mitmproxy 导入 ────────────────────────────────

    @staticmethod
    async def capture_from_proxy_log(proxy_cookie_json: str, account_id: int = 1) -> dict:
        try:
            cookies = json.loads(proxy_cookie_json)
            cookies.pop("_meta", None)
            count = await CookieManager.save_batch(cookies, account_id)
            return {
                "success": True,
                "imported_count": count,
                "validation": await CookieManager.validate_required(account_id),
                "account_id": account_id,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    async def load_proxy_cookies_from_file(path: str = "cookies.json",
                                           account_id: int = 1) -> dict:
        if not os.path.exists(path):
            return {"success": False, "error": f"文件不存在: {path}"}
        with open(path, "r") as f:
            raw = f.read()
        return await GoogleOAuthService.capture_from_proxy_log(raw, account_id)

    @staticmethod
    async def get_oauth_status() -> dict:
        cfg = await GoogleOAuthService.get_config()
        validation = await CookieManager.validate_required()
        return {
            "configured": bool(cfg["client_id"]),
            "client_id_set": bool(cfg["client_id"]),
            "client_secret_set": bool(cfg["client_secret"]),
            "redirect_uri": cfg["redirect_uri"],
            "logged_in_email": await get_config("google_user_email", ""),
            "session_valid": validation["session_key_set"],
            "validation": validation,
        }
