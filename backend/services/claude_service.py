"""
Claude API 代理服务 v2.0
新增：多账号自动切换、模型列表管理、失败重试、org_uuid 自动探测
"""
import httpx, json, uuid, time
from datetime import datetime
from typing import AsyncGenerator, Optional, Dict
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.cookie_service import CookieManager
from services.account_service import AccountManager
from database import db_execute, db_fetchall, get_config

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Origin": "https://claude.ai",
    "Referer": "https://claude.ai/",
}


def gen_uuid() -> str:
    return str(uuid.uuid4())


async def _build_headers(account_id: int = 1, extra: Optional[Dict] = None) -> Dict:
    cookie_str = await CookieManager.build_cookie_header(account_id)
    h = {**BROWSER_HEADERS, "Cookie": cookie_str, "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


async def _log(method, endpoint, status, duration,
               req_body="", resp_body="", error="", account_id=1):
    await db_execute(
        "INSERT INTO request_logs(account_id,method,endpoint,status_code,duration_ms,request_body,response_body,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (account_id, method, endpoint, status, duration,
         req_body[:2000], resp_body[:2000], error, datetime.utcnow().isoformat()))


class ClaudeAPIService:

    # ── 连接检测 ─────────────────────────────────────────────

    @staticmethod
    async def check_auth(account_id: int = 1) -> dict:
        headers = await _build_headers(account_id, {"Accept": "application/json"})
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get("https://claude.ai/api/auth/session", headers=headers)
            duration = int((time.time() - start) * 1000)
            await _log("GET", "/auth/session", resp.status_code, duration, account_id=account_id)
            body = {}
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    pass
            return {
                "authenticated": resp.status_code == 200,
                "status": resp.status_code,
                "data": body,
                "account_id": account_id,
            }
        except Exception as e:
            await _log("GET", "/auth/session", 0, 0, error=str(e), account_id=account_id)
            return {"authenticated": False, "error": str(e), "account_id": account_id}

    # ── org_uuid 自动探测 ─────────────────────────────────────

    @staticmethod
    async def get_org_uuid(account_id: int = 1) -> str:
        """
        优先级：账号表 > lastActiveOrg Cookie > API 自动查询
        """
        # 1. 账号表
        account = await AccountManager.get_account(account_id)
        if account and account.get("org_uuid"):
            return account["org_uuid"]

        # 2. lastActiveOrg Cookie
        org_from_cookie = await CookieManager.get_cookie("lastActiveOrg", account_id)
        if org_from_cookie:
            # 回写到账号表
            await AccountManager.update_account(account_id, org_uuid=org_from_cookie)
            return org_from_cookie

        # 3. 全局配置
        org_from_config = await get_config("default_org_uuid", "")
        if org_from_config:
            return org_from_config

        # 4. API 查询
        try:
            headers = await _build_headers(account_id, {"Accept": "application/json"})
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get("https://claude.ai/api/organizations", headers=headers)
            if resp.status_code == 200:
                orgs = resp.json()
                if orgs and isinstance(orgs, list) and orgs[0].get("uuid"):
                    org_uuid = orgs[0]["uuid"]
                    await AccountManager.update_account(account_id, org_uuid=org_uuid)
                    return org_uuid
        except Exception:
            pass

        return ""

    # ── 模型管理 ─────────────────────────────────────────────

    @staticmethod
    async def list_models() -> list:
        rows = await db_fetchall(
            "SELECT model_id, display_name, description, context_length FROM models WHERE is_active=1 ORDER BY sort_order"
        )
        return rows

    # ── 对话创建 ─────────────────────────────────────────────

    @staticmethod
    async def create_conversation(org_uuid: str, account_id: int = 1) -> dict:
        headers = await _build_headers(account_id)
        conv_uuid = gen_uuid()
        payload = {"uuid": conv_uuid, "name": ""}
        url = f"https://claude.ai/api/organizations/{org_uuid}/chat_conversations"
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=headers, json=payload)
            await _log("POST", "/chat_conversations", resp.status_code,
                       int((time.time() - start) * 1000), json.dumps(payload), account_id=account_id)
            return {"success": resp.status_code in (200, 201), "conversation_uuid": conv_uuid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 标题生成 ─────────────────────────────────────────────

    @staticmethod
    async def generate_title(org_uuid: str, conv_uuid: str, account_id: int = 1) -> dict:
        headers = await _build_headers(account_id)
        url = f"https://claude.ai/api/organizations/{org_uuid}/chat_conversations/{conv_uuid}/title"
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=headers, json={})
            await _log("POST", "/title", resp.status_code,
                       int((time.time() - start) * 1000), account_id=account_id)
            return resp.json() if resp.status_code == 200 else {}
        except Exception as e:
            return {"error": str(e)}

    # ── 对话树 ───────────────────────────────────────────────

    @staticmethod
    async def get_conversation_tree(org_uuid: str, conv_uuid: str, account_id: int = 1) -> dict:
        headers = await _build_headers(account_id)
        url = (f"https://claude.ai/api/organizations/{org_uuid}/chat_conversations"
               f"/{conv_uuid}?tree=True&rendering_mode=messages&consistency=eventual")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)
            await _log("GET", f"/{conv_uuid}?tree=True", resp.status_code, 0, account_id=account_id)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    # ── SSE 流式对话（核心，支持多账号自动切换）────────────────

    @staticmethod
    async def completion_stream(
        org_uuid: str, conv_uuid: str, prompt: str,
        model: str = "claude-sonnet-4-6",
        parent_message_uuid: str = "00000000-0000-0000-0000-000000000000",
        locale: str = "zh-CN", timezone: str = "Asia/Shanghai",
        attachments: list = None, files: list = None,
        tools_enabled: bool = True, personalized_styles: list = None,
        account_id: int = 1,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        headers = await _build_headers(
            account_id,
            {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        )
        human_uuid = gen_uuid()
        assistant_uuid = gen_uuid()

        # 组装 tools 列表
        tools = []
        if tools_enabled:
            tools = [{"type": "web_search_v0", "name": "web_search"}]

        payload = {
            "prompt": prompt,
            "model": model,
            "parent_message_uuid": parent_message_uuid,
            "locale": locale,
            "timezone": timezone,
            "attachments": attachments or [],
            "files": files or [],
            "rendering_mode": "messages",
            "personalized_styles": personalized_styles or [],
            "sync_sources": [],
            "tools": tools,
            "turn_message_uuids": {
                "human_message_uuid": human_uuid,
                "assistant_message_uuid": assistant_uuid,
            },
        }

        # 若有 system prompt（OpenAI 兼容层传入）
        if system_prompt:
            payload["system_prompt"] = system_prompt

        url = (f"https://claude.ai/api/organizations/{org_uuid}"
               f"/chat_conversations/{conv_uuid}/completion")
        req_body = json.dumps(payload)
        start = time.time()

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, headers=headers, content=req_body) as resp:
                    if resp.status_code == 429:
                        await AccountManager.record_failure(account_id)
                        yield f'data: {{"type":"error","status":429,"message":"账号 {account_id} 请求频率过高，请稍候"}}\n\n'
                        return

                    if resp.status_code == 403:
                        await AccountManager.record_failure(account_id)
                        yield f'data: {{"type":"error","status":403,"message":"账号 {account_id} Cookie 已失效，请刷新"}}\n\n'
                        return

                    if resp.status_code != 200:
                        err = await resp.aread()
                        await _log("POST(SSE)", "/completion", resp.status_code,
                                   int((time.time() - start) * 1000), req_body,
                                   err.decode(), account_id=account_id)
                        yield f'data: {{"type":"error","status":{resp.status_code},"body":"{err.decode()[:200]}"}}\n\n'
                        return

                    full_text = ""
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            yield line + "\n\n"
                            try:
                                data = json.loads(line[5:].strip())
                                if data.get("type") == "content_block_delta":
                                    delta = data.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        full_text += delta.get("text", "")
                            except Exception:
                                pass

                    await _log("POST(SSE)", "/completion", 200,
                               int((time.time() - start) * 1000), req_body,
                               full_text[:500], account_id=account_id)

                    # 成功时重置失败计数
                    await AccountManager.reset_failures(account_id)

                    yield (f'data: {{"type":"turn_complete",'
                           f'"human_uuid":"{human_uuid}",'
                           f'"assistant_uuid":"{assistant_uuid}",'
                           f'"account_id":{account_id}}}\n\n')

        except httpx.ConnectError as e:
            yield f'data: {{"type":"error","message":"连接失败: {str(e)}"}}\n\n'
        except Exception as e:
            yield f'data: {{"type":"error","message":"{str(e)}"}}\n\n'

    # ── 文件上传 ─────────────────────────────────────────────

    @staticmethod
    async def upload_file(org_uuid: str, file_content: bytes,
                          original_name: str, content_type: str,
                          account_id: int = 1) -> dict:
        ts = int(time.time() * 1000)
        stored_name = f"{ts}_{original_name}"
        cookie_str = await CookieManager.build_cookie_header(account_id)
        headers = {**BROWSER_HEADERS, "Cookie": cookie_str}
        url = f"https://claude.ai/api/organizations/{org_uuid}/upload"
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url, headers=headers,
                    files={"file": (stored_name, file_content, content_type)})
            await _log("POST", "/upload-file", resp.status_code,
                       int((time.time() - start) * 1000), account_id=account_id)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "file_uuid": data.get("file_uuid", gen_uuid()),
                    "file_name": stored_name,
                    "sanitized_name": stored_name,
                    "file_kind": data.get("file_kind", "image"),
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "size_bytes": len(file_content),
                }
            return {"success": False, "status": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}
