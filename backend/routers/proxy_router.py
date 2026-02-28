"""
代理路由 /api/proxy v2.0
透传任意 claude.ai API 请求，供请求调试器使用
支持 account_id 参数，使用对应账号的 Cookie
"""
import httpx
import json
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

from services.cookie_service import CookieManager
from database import db_execute
from datetime import datetime

router = APIRouter()

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Origin": "https://claude.ai",
    "Referer": "https://claude.ai/",
}


class ProxyRequest(BaseModel):
    method: str = "GET"
    endpoint: str            # 例如 /api/auth/session
    payload: Optional[Dict[str, Any]] = None
    extra_headers: Optional[Dict[str, str]] = None
    account_id: Optional[int] = 1


@router.post("/raw", summary="透传请求到 claude.ai（支持账号选择）")
async def proxy_raw(req: ProxyRequest):
    """
    直接透传请求到 claude.ai，自动附加指定账号的 Cookie 头。
    用于请求调试器测试任意接口。
    """
    account_id = req.account_id or 1
    cookie_str = await CookieManager.build_cookie_header(account_id)
    headers = {
        **BROWSER_HEADERS,
        "Cookie": cookie_str,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if req.extra_headers:
        headers.update(req.extra_headers)

    url = f"https://claude.ai{req.endpoint}"
    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            if req.method.upper() == "GET":
                resp = await client.get(url, headers=headers)
            elif req.method.upper() == "POST":
                resp = await client.post(url, headers=headers,
                                          content=json.dumps(req.payload or {}))
            elif req.method.upper() == "DELETE":
                resp = await client.delete(url, headers=headers)
            elif req.method.upper() == "PATCH":
                resp = await client.patch(url, headers=headers,
                                           content=json.dumps(req.payload or {}))
            else:
                raise HTTPException(400, f"不支持的 HTTP 方法: {req.method}")

        duration = int((time.time() - start) * 1000)

        try:
            body = resp.json()
        except Exception:
            body = resp.text

        await db_execute(
            "INSERT INTO request_logs (account_id, method, endpoint, status_code, duration_ms, request_body, response_body, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (account_id, req.method.upper(), req.endpoint, resp.status_code, duration,
             json.dumps(req.payload or {})[:1000],
             json.dumps(body)[:1000] if isinstance(body, dict) else str(body)[:1000],
             datetime.utcnow().isoformat())
        )

        return {
            "status_code": resp.status_code,
            "duration_ms": duration,
            "headers": dict(resp.headers),
            "body": body,
        }

    except httpx.ConnectError as e:
        return {
            "status_code": 0,
            "error": f"连接失败（网络不通或需要代理）: {str(e)}",
            "tip": "本环境网络访问受限，实际部署后可正常连接 claude.ai",
        }
    except Exception as e:
        return {"status_code": 0, "error": str(e)}


@router.get("/versions", summary="版本检查轮询（模拟 /versions?source=w）")
async def check_versions():
    return {"source": "w", "version": "latest", "needs_refresh": False}
