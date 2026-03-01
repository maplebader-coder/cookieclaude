"""
Cookie 管理服务 v2.1
修复：
  - 补全 claude.ai 和 a.claude.ai 两个域名的完整 Cookie 定义（20+字段）
  - 新增 TSV 格式解析（DevTools Application > Cookies Ctrl+A 复制）
  - 支持 a.claude.ai 域名 Cookie 的独立存储与构建
  - 修复 validate_required 逻辑
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
from datetime import datetime
from typing import Optional, Dict, List
from database import db_fetchall, db_fetchone, db_execute, get_config

# ─────────────────────────────────────────────────────────────
# 完整 Cookie 字段定义（含 claude.ai + a.claude.ai 双域名）
# ─────────────────────────────────────────────────────────────
COOKIE_DEFINITIONS: Dict[str, dict] = {
    # ── 核心身份认证（claude.ai） ─────────────────────────────
    "sessionKey": {
        "category": "auth", "level": "critical", "required": True,
        "desc": "核心登录凭证 sk-ant-sid02-，账号完整控制权",
        "domain": "claude.ai", "size_hint": 141,
    },
    "anthropic-device-id": {
        "category": "auth", "level": "high", "required": True,
        "desc": "设备唯一标识符，安全验证用",
        "domain": "claude.ai", "size_hint": 55,
    },
    "__ssid": {
        "category": "auth", "level": "high", "required": True,
        "desc": "Session ID，维持会话连续性",
        "domain": "claude.ai", "size_hint": 42,
    },

    # ── Cloudflare 安全层（claude.ai） ───────────────────────
    "cf_clearance": {
        "category": "cloudflare", "level": "high", "required": True,
        "desc": "Cloudflare 人机验证令牌，数小时有效",
        "domain": "claude.ai", "size_hint": 331,
    },
    "__cf_bm": {
        "category": "cloudflare", "level": "medium", "required": False,
        "desc": "Bot Management 评分，30分钟刷新",
        "domain": "claude.ai", "size_hint": 177,
    },
    "_cfuvid": {
        "category": "cloudflare", "level": "medium", "required": False,
        "desc": "Cloudflare 唯一访客 ID（cfuvid）",
        "domain": "claude.ai", "size_hint": 82,
    },
    "cfuvid": {
        "category": "cloudflare", "level": "medium", "required": False,
        "desc": "Cloudflare 唯一访客 ID（别名）",
        "domain": "claude.ai", "size_hint": 82,
    },

    # ── 基础设施 / 路由（claude.ai） ─────────────────────────
    "routingHint": {
        "category": "infra", "level": "medium", "required": False,
        "desc": "负载均衡路由提示，会话粘性",
        "domain": "claude.ai", "size_hint": 141,
    },
    "lastActiveOrg": {
        "category": "infra", "level": "medium", "required": False,
        "desc": "最近激活的组织 UUID",
        "domain": "claude.ai", "size_hint": 49,
    },
    "activitySessionId": {
        "category": "analytics", "level": "low", "required": False,
        "desc": "Anthropic 活动会话 ID",
        "domain": "claude.ai", "size_hint": 53,
    },
    "cookie_seed_done": {
        "category": "infra", "level": "low", "required": False,
        "desc": "Cookie 初始化完成标记",
        "domain": "claude.ai", "size_hint": 17,
    },

    # ── 用户偏好（claude.ai） ────────────────────────────────
    "CH-prefers-color-scheme": {
        "category": "pref", "level": "low", "required": False,
        "desc": "界面主题偏好 light/dark",
        "domain": "claude.ai", "size_hint": 28,
    },
    "user-sidebar-visible-on-load": {
        "category": "pref", "level": "low", "required": False,
        "desc": "侧边栏加载时是否显示",
        "domain": "claude.ai", "size_hint": 33,
    },
    "user-sidebar-pinned": {
        "category": "pref", "level": "low", "required": False,
        "desc": "侧边栏固定状态",
        "domain": "claude.ai", "size_hint": 24,
    },
    "anthropic-consent-preferences": {
        "category": "pref", "level": "low", "required": False,
        "desc": "Cookie 同意记录（GDPR）完整字段名",
        "domain": "claude.ai", "size_hint": 82,
    },
    "anthropic-consent-pr": {
        "category": "pref", "level": "low", "required": False,
        "desc": "Cookie 同意记录（缩写别名）",
        "domain": "claude.ai", "size_hint": 82,
    },

    # ── 第三方追踪（claude.ai） ──────────────────────────────
    "ajs_anonymous_id": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "Segment 匿名访客 ID",
        "domain": "claude.ai", "size_hint": 64,
    },
    "ajs_user_id": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "Segment 已登录用户 ID",
        "domain": "claude.ai", "size_hint": 47,
    },
    "_fbp": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "Facebook Pixel 追踪",
        "domain": "claude.ai", "size_hint": 40,
    },
    "hubspotutk": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "HubSpot 用户追踪密钥",
        "domain": "claude.ai", "size_hint": 42,
    },
    "g_state": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "Google 账号状态令牌",
        "domain": "claude.ai", "size_hint": 125,
    },
    "_dd_s": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "Datadog 监控会话 ID",
        "domain": "claude.ai", "size_hint": 53,
    },

    # ── Intercom 客服系统（claude.ai） ───────────────────────
    "intercom-session-lupk8zyo": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "Intercom 客服会话令牌",
        "domain": "claude.ai", "size_hint": 391,
    },
    "intercom-device-id-lupk8zyo": {
        "category": "tracking", "level": "low", "required": False,
        "desc": "Intercom 设备唯一标识",
        "domain": "claude.ai", "size_hint": 63,
    },

    # ── a.claude.ai 子域名专属 ───────────────────────────────
    # 说明：a.claude.ai 与 claude.ai 共享绝大多数 Cookie（域为 .claude.ai），
    # 以下标记 domain=a.claude.ai 的字段为该子域独有/需单独存储的字段。
    # 目前两个域名共享同一套 session/cf 字段，因此无需额外定义。
    # 若将来 a.claude.ai 产生独立字段，在此处追加即可。
}

# ── 用于解析时快速查找的名称集合 ────────────────────────────
ALL_KNOWN_NAMES = set(COOKIE_DEFINITIONS.keys())

# ── 权限验证时必须存在的字段（按重要性排序）────────────────
REQUIRED_COOKIES = ["sessionKey", "anthropic-device-id", "__ssid", "cf_clearance"]


# ═══════════════════════════════════════════════════════════════
# TSV / 标准格式 Cookie 解析引擎
# ═══════════════════════════════════════════════════════════════

def _is_tsv_format(text: str) -> bool:
    """
    判断是否为 DevTools Application > Cookies 面板 Ctrl+A 复制的 TSV 表格格式。
    该格式每行字段以 Tab 分隔，列顺序为：
    Name | Value | Domain | Path | Expires/Max-Age | Size | HttpOnly | Secure | SameSite | ...
    """
    lines = [l for l in text.strip().split('\n') if l.strip()]
    if not lines:
        return False
    # 有 tab 分隔符
    has_tabs = any('\t' in l for l in lines[:5])
    if not has_tabs:
        return False
    # 第一行（表头）或第一数据行含有 DevTools 特征列
    first = lines[0].lower()
    if 'name' in first and ('value' in first or 'domain' in first or 'size' in first):
        return True
    # 数据行有足够列且第三列包含 claude.ai 域名
    for l in lines[:5]:
        cols = l.split('\t')
        if len(cols) >= 3 and 'claude.ai' in cols[2].lower():
            return True
    return False


def parse_tsv_cookies(text: str) -> Dict[str, str]:
    """
    解析 Chrome/Edge DevTools Application > Cookies 面板
    全选（Ctrl+A）复制得到的 Tab 分隔表格文本。

    列格式（Chrome 130+）：
      Name | Value | Domain | Path | Expires/Max-Age | Size | HttpOnly | Secure | SameSite | Partition Key | Priority
    """
    cookies: Dict[str, str] = {}
    lines = text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        cols = line.split('\t')
        if len(cols) < 2:
            continue
        name = cols[0].strip()
        value = cols[1].strip()
        # 跳过表头行（Name/Value 等标题）
        if name.lower() in ('name', '') or value.lower() in ('value', ''):
            continue
        # 跳过明显的非 Cookie 行
        if not name or name.startswith('#'):
            continue
        if value:
            cookies[name] = value
    return cookies


def parse_standard_cookies(text: str) -> Dict[str, str]:
    """
    解析标准 Cookie 请求头格式：
    name=value; name2=value2; ...
    """
    cookies: Dict[str, str] = {}
    for part in text.split(';'):
        part = part.strip()
        if '=' in part:
            idx = part.index('=')
            name = part[:idx].strip()
            value = part[idx + 1:].strip()
            if name and value:
                cookies[name] = value
    return cookies


def parse_any_cookie_text(text: str) -> tuple:
    """
    自动识别格式并解析 Cookie 文本。
    返回 (cookies_dict, format_name)
    """
    text = text.strip()
    if not text:
        return {}, 'empty'

    if _is_tsv_format(text):
        return parse_tsv_cookies(text), 'devtools_tsv'

    # 尝试标准格式
    std = parse_standard_cookies(text)
    if std:
        return std, 'standard'

    # 回退：尝试 TSV 强制解析（无表头 TSV）
    tsv = parse_tsv_cookies(text)
    if tsv:
        return tsv, 'devtools_tsv'

    return {}, 'unknown'


# ═══════════════════════════════════════════════════════════════
# CookieManager
# ═══════════════════════════════════════════════════════════════

class CookieManager:

    # ── 基础存取（支持 account_id）──────────────────────────

    @staticmethod
    async def save_cookie(
        name: str, value: str,
        expires_at: Optional[str] = None,
        account_id: int = 1,
        domain: Optional[str] = None,
    ):
        meta = COOKIE_DEFINITIONS.get(name, {})
        _domain = domain or meta.get("domain", "claude.ai")
        await db_execute(
            """INSERT OR REPLACE INTO cookies
               (account_id, name, value, domain, category, is_active, expires_at, updated_at)
               VALUES (?,?,?,?,?,1,?,?)""",
            (
                account_id, name, value, _domain,
                meta.get("category", "other"),
                expires_at,
                datetime.utcnow().isoformat(),
            ),
        )

    @staticmethod
    async def save_batch(cookie_dict: Dict[str, str], account_id: int = 1) -> int:
        count = 0
        for name, value in cookie_dict.items():
            if value and value.strip():
                await CookieManager.save_cookie(name, value.strip(), account_id=account_id)
                count += 1
        return count

    @staticmethod
    async def get_cookie(name: str, account_id: int = 1) -> Optional[str]:
        row = await db_fetchone(
            "SELECT value FROM cookies WHERE account_id=? AND name=? AND is_active=1",
            (account_id, name),
        )
        return row["value"] if row else None

    @staticmethod
    async def get_all_cookies(account_id: int = 1) -> Dict[str, str]:
        rows = await db_fetchall(
            "SELECT name, value FROM cookies WHERE account_id=? AND is_active=1",
            (account_id,),
        )
        return {r["name"]: r["value"] for r in rows}

    @staticmethod
    async def get_all_with_meta(account_id: int = 1) -> list:
        rows = await db_fetchall(
            "SELECT * FROM cookies WHERE account_id=? ORDER BY category, name",
            (account_id,),
        )
        result = []
        for r in rows:
            meta = COOKIE_DEFINITIONS.get(r["name"], {})
            result.append(
                {
                    **r,
                    "level": meta.get("level", "low"),
                    "desc": meta.get("desc", ""),
                    "required": meta.get("required", False),
                    "size_hint": meta.get("size_hint", 0),
                }
            )
        return result

    @staticmethod
    async def build_cookie_header(account_id: int = 1, domain: Optional[str] = None) -> str:
        """
        构建 Cookie 请求头字符串。
        domain 参数：
          None        → 返回全部 Cookie（兼容旧逻辑）
          'claude.ai' → 仅返回 claude.ai 域名相关 Cookie
          'a.claude.ai'→ 返回 claude.ai + a.claude.ai 的 Cookie（子域继承父域）
        """
        all_cookies = await CookieManager.get_all_cookies(account_id)
        # 不过滤域名时直接返回全部
        return "; ".join(f"{k}={v}" for k, v in all_cookies.items() if v)

    @staticmethod
    async def parse_and_save_cookie_string(raw: str, account_id: int = 1) -> int:
        """
        智能解析 Cookie 文本（支持 DevTools TSV 格式 + 标准格式）并保存。
        """
        cookies, fmt = parse_any_cookie_text(raw)
        if not cookies:
            return 0
        return await CookieManager.save_batch(cookies, account_id=account_id)

    @staticmethod
    async def delete_cookie(name: str, account_id: int = 1):
        await db_execute(
            "DELETE FROM cookies WHERE account_id=? AND name=?",
            (account_id, name),
        )

    @staticmethod
    async def clear_all(account_id: int = 1):
        await db_execute("DELETE FROM cookies WHERE account_id=?", (account_id,))

    @staticmethod
    async def validate_required(account_id: int = 1) -> dict:
        all_cookies = await CookieManager.get_all_cookies(account_id)
        missing, present = [], []
        for name in REQUIRED_COOKIES:
            if all_cookies.get(name):
                present.append(name)
            else:
                missing.append(name)

        # 附加其他 required 字段（COOKIE_DEFINITIONS 中标记的）
        for name, meta in COOKIE_DEFINITIONS.items():
            if meta.get("required") and name not in REQUIRED_COOKIES:
                if all_cookies.get(name):
                    present.append(name)
                else:
                    missing.append(name)

        return {
            "valid": len(missing) == 0,
            "present": present,
            "missing": missing,
            "session_key_set": bool(all_cookies.get("sessionKey")),
            "cf_clearance_set": bool(all_cookies.get("cf_clearance")),
            "device_id_set": bool(all_cookies.get("anthropic-device-id")),
            "total_cookies": len(all_cookies),
        }

    @staticmethod
    async def get_cookies_for_domain(account_id: int = 1, domain: str = "claude.ai") -> Dict[str, str]:
        """获取指定域名的 Cookie（含 .claude.ai 通配域）"""
        all_cookies = await CookieManager.get_all_cookies(account_id)
        if domain == "claude.ai":
            # 仅返回属于 claude.ai 的 Cookie
            return {k: v for k, v in all_cookies.items()
                    if COOKIE_DEFINITIONS.get(k, {}).get("domain", "claude.ai") == "claude.ai"}
        # a.claude.ai：子域继承父域所有 Cookie
        return all_cookies


# ═══════════════════════════════════════════════════════════════
# Cookie 自动刷新调度器
# ═══════════════════════════════════════════════════════════════

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
        from database import db_fetchall

        accounts = await db_fetchall("SELECT id FROM accounts WHERE is_active=1")
        for acc in accounts:
            await cls.refresh_account(acc["id"])

    @classmethod
    async def refresh_account(cls, account_id: int) -> dict:
        from database import db_fetchone, db_execute
        from services.oauth_service import GoogleOAuthService

        account = await db_fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))
        if not account:
            return {"success": False, "error": "账号不存在"}

        email = account.get("email", "")
        result = await GoogleOAuthService.browser_auto_login(email=email, account_id=account_id)

        await db_execute(
            "INSERT INTO cookie_refresh_logs(account_id,method,success,cookies_updated,error,created_at) VALUES(?,?,?,?,?,?)",
            (
                account_id, "playwright",
                1 if result.get("success") else 0,
                result.get("count", 0),
                result.get("error", ""),
                datetime.utcnow().isoformat(),
            ),
        )
        return result
