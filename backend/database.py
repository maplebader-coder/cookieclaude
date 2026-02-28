"""
数据库初始化与操作层 (SQLite via aiosqlite)
存储：Cookie配置、对话历史、请求日志、用户设置、多账号管理
v2.0 - 新增多账号、模型配置、Cookie刷新日志表
"""
import aiosqlite
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "claude_client.db")


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # ── 多账号表 ──────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT DEFAULT '',
                org_uuid TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                weight INTEGER DEFAULT 1,
                last_used_at TEXT,
                fail_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")

        # ── Cookie 表（关联账号）────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cookies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                domain TEXT DEFAULT 'claude.ai',
                category TEXT DEFAULT 'auth',
                is_active INTEGER DEFAULT 1,
                expires_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, name)
            )""")

        # ── 对话表 ────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                uuid TEXT PRIMARY KEY,
                title TEXT DEFAULT '新建对话',
                model TEXT DEFAULT 'claude-sonnet-4-6',
                parent_message_uuid TEXT DEFAULT '00000000-0000-0000-0000-000000000000',
                organization_uuid TEXT,
                account_id INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")

        # ── 消息表 ────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                uuid TEXT PRIMARY KEY,
                conversation_uuid TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                parent_uuid TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_uuid) REFERENCES conversations(uuid)
            )""")

        # ── 请求日志表 ────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER DEFAULT 1,
                method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                status_code INTEGER,
                duration_ms INTEGER,
                request_body TEXT,
                response_body TEXT,
                error TEXT,
                created_at TEXT NOT NULL
            )""")

        # ── 应用配置表 ────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")

        # ── 文件上传表 ────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_files (
                file_uuid TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                sanitized_name TEXT NOT NULL,
                file_kind TEXT DEFAULT 'image',
                size_bytes INTEGER,
                conversation_uuid TEXT,
                created_at TEXT NOT NULL
            )""")

        # ── Cookie 刷新日志表 ─────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cookie_refresh_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                method TEXT NOT NULL,
                success INTEGER DEFAULT 0,
                cookies_updated INTEGER DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL
            )""")

        # ── 模型配置表 ────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                description TEXT DEFAULT '',
                context_length INTEGER DEFAULT 200000,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0
            )""")

        # ── 插入默认账号 ──────────────────────────────────────
        await db.execute("""
            INSERT OR IGNORE INTO accounts(id,name,email,is_active,weight,created_at,updated_at)
            VALUES(1,'默认账号','',1,1,?,?)""",
            (datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))

        # ── 插入默认模型列表 ──────────────────────────────────
        default_models = [
            ("claude-opus-4-5",         "Claude Opus 4.5",        "最强推理能力", 200000, 1, 1),
            ("claude-sonnet-4-5",       "Claude Sonnet 4.5",      "速度与能力平衡", 200000, 1, 2),
            ("claude-haiku-4-5",        "Claude Haiku 4.5",       "快速轻量", 200000, 1, 3),
            ("claude-sonnet-4-6",       "Claude Sonnet 4.6",      "最新Sonnet", 200000, 1, 4),
            ("claude-opus-4-6",         "Claude Opus 4.6",        "最新Opus旗舰", 200000, 1, 5),
        ]
        for m in default_models:
            await db.execute(
                "INSERT OR IGNORE INTO models(model_id,display_name,description,context_length,is_active,sort_order) VALUES(?,?,?,?,?,?)",
                m)

        # ── 应用默认配置 ──────────────────────────────────────
        defaults = {
            "base_url":             "https://claude.ai",
            "api_subdomain":        "https://a.claude.ai",
            "default_model":        "claude-sonnet-4-6",
            "default_locale":       "zh-CN",
            "default_timezone":     "Asia/Shanghai",
            "rendering_mode":       "messages",
            "tools_enabled":        json.dumps([{"type": "web_search_v0", "name": "web_search"}]),
            "default_org_uuid":     "",
            "google_client_id":     "",
            "google_client_secret": "",
            "oauth_redirect_uri":   "http://localhost:8765/api/auth/google/callback",
            # 轮询策略: round_robin | weighted | least_fail
            "account_poll_strategy": "round_robin",
            # Cookie 自动刷新
            "cookie_auto_refresh":   "false",
            "cookie_refresh_interval_hours": "6",
            # OpenAI 兼容层
            "openai_api_key":        "sk-claude-proxy-default",
            "openai_require_auth":   "true",
            # 速率限制
            "rate_limit_per_minute": "30",
        }
        for key, value in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO app_config(key,value,updated_at) VALUES(?,?,?)",
                (key, value, datetime.utcnow().isoformat()))

        await db.commit()


# ── 通用查询工具 ──────────────────────────────────────────────

async def db_fetchone(sql: str, params: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def db_fetchall(sql: str, params: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def db_execute(sql: str, params: tuple = ()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid


async def get_config(key: str, default=None):
    row = await db_fetchone("SELECT value FROM app_config WHERE key=?", (key,))
    return row["value"] if row else default


async def set_config(key: str, value: str):
    await db_execute(
        "INSERT OR REPLACE INTO app_config(key,value,updated_at) VALUES(?,?,?)",
        (key, value, datetime.utcnow().isoformat()))
