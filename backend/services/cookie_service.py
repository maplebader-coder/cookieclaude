"""
Cookie 管理服务 v2.0
新增：多账号 Cookie 隔离、Cookie 自动刷新调度器
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict
from database import db_fetchall, db_fetchone, db_execute, get_config

COOKIE_DEFINITIONS = {
    "sessionKey":               {"category": "auth",       "level": "critical", "required": True,  "desc": "核心登录凭证 sk-ant-sid02-，账号完整控制权",  "domain": "claude.ai", "size_hint": 141},
    "anthropic-device-id":      {"category": "auth",       "level": "high",     "required": True,  "desc": "设备唯一标识符，安全验证用",                  "domain": "claude.ai", "size_hint": 55},
    "__ssid":                   {"category": "auth",       "level": "high",     "required": True,  "desc": "Session ID，维持会话连续性",                  "domain": "claude.ai", "size_hint": 42},
    "cf_clearance":             {"category": "cloudflare", "level": "high",     "required": True,  "desc": "Cloudflare 人机验证令牌，数小时有效",          "domain": "claude.ai", "size_hint": 310},
    "__cf_bm":                  {"category": "cloudflare", "level": "medium",   "required": False, "desc": "Bot Management 评分，30分钟刷新",             "domain": "claude.ai", "size_hint": 177},
    "_cfuvid":                  {"category": "cloudflare", "level": "medium",   "required": False, "desc": "Cloudflare 唯一访客 ID",                      "domain": "claude.ai", "size_hint": 82},
    "routingHint":              {"category": "infra",      "level": "medium",   "required": False, "desc": "负载均衡路由提示，会话粘性",                  "domain": "claude.ai", "size_hint": 505},
    "lastActiveOrg":            {"category": "infra",      "level": "medium",   "required": False, "desc": "最近激活的组织 UUID",                         "domain": "claude.ai", "size_hint": 49},
    "activitySessionId":        {"category": "analytics",  "level": "low",      "required": False, "desc": "Anthropic 活动会话 ID",                       "domain": "claude.ai", "size_hint": 53},
    "CH-prefers-color-scheme":  {"category": "pref",       "level": "low",      "required": False, "desc": "界面主题 light/dark",                         "domain": "claude.ai", "size_hint": 28},
    "anthropic-consent-pr":     {"category": "pref",       "level": "low",      "required": False, "desc": "Cookie 同意记录（GDPR）",                    "domain": "claude.ai", "size_hint": 82},
    "ajs_anonymous_id":         {"category": "tracking",   "level": "low",      "required": False, "desc": "Segment 匿名访客 ID",                         "domain": "claude.ai", "size_hint": 64},
    "ajs_user_id":              {"category": "tracking",   "level": "low",      "required": False, "desc": "Segment 已登录用户 ID",                       "domain": "claude.ai", "size_hint": 47},
    "hubspotutk":               {"category": "tracking",   "level": "low",      "required": False, "desc": "HubSpot 用户追踪密钥",                        "domain": "claude.ai", "size_hint": 42},
    "_fbp":                     {"category": "tracking",   "level": "low",      "required": False, "desc": "Facebook Pixel 追踪",                         "domain": "claude.ai", "size_hint": 40},
    "user-sidebar-pinned":      {"category": "pref",       "level": "low",      "required": False, "desc": "侧边栏固定状态",                              "domain": "claude.ai", "size_hint": 24},
}


class CookieManager:

    # ── 基础存取（支持 account_id）──────────────────────────

    @staticmethod
    async def save_cookie(name: str, value: str, expires_at: Optional[str] = None, account_id: int = 1):
        meta = COOKIE_DEFINITIONS.get(name, {})
        await db_execute(
            "INSERT OR REPLACE INTO cookies(account_id,name,value,domain,category,is_active,expires_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
            (account_id, name, value, meta.get("domain", "claude.ai"),
             meta.get("category", "other"), expires_at, datetime.utcnow().isoformat()))

    @staticmethod
    async def save_batch(cookie_dict: Dict[str, str], account_id: int = 1) -> int:
        count = 0
        for name, value in cookie_dict.items():
            if value:
                await CookieManager.save_cookie(name, value, account_id=account_id)
                count += 1
        return count

    @staticmethod
    async def get_cookie(name: str, account_id: int = 1) -> Optional[str]:
        row = await db_fetchone(
            "SELECT value FROM cookies WHERE account_id=? AND name=? AND is_active=1",
            (account_id, name))
        return row["value"] if row else None

    @staticmethod
    async def get_all_cookies(account_id: int = 1) -> Dict[str, str]:
        rows = await db_fetchall(
            "SELECT name,value FROM cookies WHERE account_id=? AND is_active=1",
            (account_id,))
        return {r["name"]: r["value"] for r in rows}

    @staticmethod
    async def get_all_with_meta(account_id: int = 1) -> list:
        rows = await db_fetchall(
            "SELECT * FROM cookies WHERE account_id=? ORDER BY category,name",
            (account_id,))
        result = []
        for r in rows:
            meta = COOKIE_DEFINITIONS.get(r["name"], {})
            result.append({**r, "level": meta.get("level", "low"),
                           "desc": meta.get("desc", ""),
                           "required": meta.get("required", False),
                           "size_hint": meta.get("size_hint", 0)})
        return result

    @staticmethod
    async def build_cookie_header(account_id: int = 1) -> str:
        all_cookies = await CookieManager.get_all_cookies(account_id)
        return "; ".join(f"{k}={v}" for k, v in all_cookies.items() if v)

    @staticmethod
    async def parse_and_save_cookie_string(raw: str, account_id: int = 1) -> int:
        parsed = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                idx = part.index("=")
                name, value = part[:idx].strip(), part[idx + 1:].strip()
                if name and value:
                    parsed[name] = value
        return await CookieManager.save_batch(parsed, account_id=account_id)

    @staticmethod
    async def delete_cookie(name: str, account_id: int = 1):
        await db_execute(
            "DELETE FROM cookies WHERE account_id=? AND name=?",
            (account_id, name))

    @staticmethod
    async def clear_all(account_id: int = 1):
        await db_execute("DELETE FROM cookies WHERE account_id=?", (account_id,))

    @staticmethod
    async def validate_required(account_id: int = 1) -> dict:
        all_cookies = await CookieManager.get_all_cookies(account_id)
        missing, present = [], []
        for name, meta in COOKIE_DEFINITIONS.items():
            if meta.get("required"):
                (present if all_cookies.get(name) else missing).append(name)
        return {
            "valid": len(missing) == 0,
            "present": present,
            "missing": missing,
            "session_key_set": bool(all_cookies.get("sessionKey")),
        }

    # ── 兼容旧接口（默认账号1）────────────────────────────────
    # 保持向后兼容，旧代码调用时无需传 account_id


class CookieRefreshScheduler:
    """
    Cookie 自动刷新调度器（基于 Playwright）。
    定期检测所有活跃账号的 cf_clearance 是否过期，
    若过期则触发浏览器静默刷新流程。
    """

    _task: Optional[asyncio.Task] = None

    @classmethod
    async def start(cls):
        enabled = await get_config("cookie_auto_refresh", "false")
        if enabled.lower() != "true":
            return
        if cls._task and not cls._task.done():
            return
        cls._task = asyncio.create_task(cls._loop())

    @classmethod
    async def stop(cls):
        if cls._task:
            cls._task.cancel()

    @classmethod
    async def _loop(cls):
        interval_h = float(await get_config("cookie_refresh_interval_hours", "6"))
        while True:
            await asyncio.sleep(interval_h * 3600)
            await cls.refresh_all_accounts()

    @classmethod
    async def refresh_all_accounts(cls):
        from database import db_fetchall, db_execute
        from datetime import datetime

        accounts = await db_fetchall(
            "SELECT id FROM accounts WHERE is_active=1"
        )
        for acc in accounts:
            await cls.refresh_account(acc["id"])

    @classmethod
    async def refresh_account(cls, account_id: int) -> dict:
        """
        使用 Playwright 无头浏览器刷新指定账号的 Cookie。
        需要预先保存 email（账号表中存储）。
        """
        from database import db_fetchone, db_execute
        from services.oauth_service import GoogleOAuthService

        account = await db_fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
        if not account:
            return {"success": False, "error": "账号不存在"}

        email = account.get("email", "")
        result = await GoogleOAuthService.browser_auto_login(email=email, account_id=account_id)

        # 记录刷新日志
        await db_execute(
            "INSERT INTO cookie_refresh_logs(account_id,method,success,cookies_updated,error,created_at) VALUES(?,?,?,?,?,?)",
            (account_id, "playwright",
             1 if result.get("success") else 0,
             result.get("count", 0),
             result.get("error", ""),
             datetime.utcnow().isoformat())
        )
        return result
