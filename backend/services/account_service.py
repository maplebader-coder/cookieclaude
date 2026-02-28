"""
多账号管理服务
功能：账号 CRUD、轮询策略（轮询/加权/最少失败）、账号健康检测
v2.0
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
from datetime import datetime
from typing import Optional, Dict, List

from database import db_execute, db_fetchall, db_fetchone, get_config, set_config

# 全局轮询游标（进程内）
_round_robin_cursor: int = 0
_cursor_lock = asyncio.Lock()


class AccountManager:

    # ── CRUD ─────────────────────────────────────────────────

    @staticmethod
    async def create_account(name: str, email: str = "", org_uuid: str = "", weight: int = 1) -> int:
        now = datetime.utcnow().isoformat()
        account_id = await db_execute(
            "INSERT INTO accounts(name,email,org_uuid,is_active,weight,fail_count,created_at,updated_at) VALUES(?,?,?,1,?,0,?,?)",
            (name, email, org_uuid, weight, now, now)
        )
        return account_id

    @staticmethod
    async def update_account(account_id: int, **kwargs) -> bool:
        allowed = {"name", "email", "org_uuid", "is_active", "weight"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [account_id]
        await db_execute(f"UPDATE accounts SET {set_clause} WHERE id=?", tuple(vals))
        return True

    @staticmethod
    async def delete_account(account_id: int) -> bool:
        if account_id == 1:
            return False  # 保护默认账号
        await db_execute("DELETE FROM accounts WHERE id=?", (account_id,))
        await db_execute("DELETE FROM cookies WHERE account_id=?", (account_id,))
        return True

    @staticmethod
    async def list_accounts() -> List[Dict]:
        rows = await db_fetchall(
            "SELECT id,name,email,org_uuid,is_active,weight,last_used_at,fail_count,created_at FROM accounts ORDER BY id"
        )
        # 附加每个账号的 Cookie 完整性状态
        for row in rows:
            validation = await AccountManager.validate_account_cookies(row["id"])
            row["cookie_valid"] = validation["valid"]
            row["session_key_set"] = validation["session_key_set"]
        return rows

    @staticmethod
    async def get_account(account_id: int) -> Optional[Dict]:
        return await db_fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))

    # ── Cookie 隔离查询 ───────────────────────────────────────

    @staticmethod
    async def get_account_cookies(account_id: int) -> Dict[str, str]:
        rows = await db_fetchall(
            "SELECT name,value FROM cookies WHERE account_id=? AND is_active=1",
            (account_id,)
        )
        return {r["name"]: r["value"] for r in rows}

    @staticmethod
    async def validate_account_cookies(account_id: int) -> Dict:
        from services.cookie_service import COOKIE_DEFINITIONS
        cookies = await AccountManager.get_account_cookies(account_id)
        required = [k for k, v in COOKIE_DEFINITIONS.items() if v.get("required")]
        missing = [k for k in required if not cookies.get(k)]
        present = [k for k in required if cookies.get(k)]
        return {
            "valid": len(missing) == 0,
            "present": present,
            "missing": missing,
            "session_key_set": bool(cookies.get("sessionKey")),
        }

    # ── 轮询策略 ─────────────────────────────────────────────

    @staticmethod
    async def pick_account() -> Optional[Dict]:
        """
        根据配置的策略选择一个可用账号。
        策略：round_robin | weighted | least_fail
        返回账号行，同时更新 last_used_at。
        """
        strategy = await get_config("account_poll_strategy", "round_robin")
        active_accounts = await db_fetchall(
            "SELECT * FROM accounts WHERE is_active=1 ORDER BY id"
        )
        # 过滤掉 Cookie 无效的账号
        valid_accounts = []
        for acc in active_accounts:
            v = await AccountManager.validate_account_cookies(acc["id"])
            if v["session_key_set"]:
                valid_accounts.append(acc)

        if not valid_accounts:
            return None

        account = None

        if strategy == "round_robin":
            global _round_robin_cursor
            async with _cursor_lock:
                idx = _round_robin_cursor % len(valid_accounts)
                account = valid_accounts[idx]
                _round_robin_cursor = (idx + 1) % len(valid_accounts)

        elif strategy == "weighted":
            import random
            weights = [a["weight"] for a in valid_accounts]
            account = random.choices(valid_accounts, weights=weights, k=1)[0]

        elif strategy == "least_fail":
            account = min(valid_accounts, key=lambda a: a["fail_count"])

        else:
            account = valid_accounts[0]

        # 更新 last_used_at
        if account:
            await db_execute(
                "UPDATE accounts SET last_used_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), account["id"])
            )

        return account

    # ── 失败计数 ─────────────────────────────────────────────

    @staticmethod
    async def record_failure(account_id: int):
        await db_execute(
            "UPDATE accounts SET fail_count = fail_count + 1 WHERE id=?",
            (account_id,)
        )

    @staticmethod
    async def reset_failures(account_id: int):
        await db_execute(
            "UPDATE accounts SET fail_count=0 WHERE id=?",
            (account_id,)
        )
