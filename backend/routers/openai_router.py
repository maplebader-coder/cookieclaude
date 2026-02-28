"""
OpenAI 兼容层路由 /v1
实现 OpenAI Chat Completions API 格式，支持以下接口：
  GET  /v1/models               — 模型列表
  POST /v1/chat/completions     — 对话补全（流式 + 非流式）

使用方式（任何 OpenAI 客户端）：
  base_url = "http://localhost:8765/v1"
  api_key  = "sk-claude-proxy-default"（或管理端配置的 key）
"""
import json
import time
import uuid
import asyncio
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from services.claude_service import ClaudeAPIService
from services.account_service import AccountManager
from services.cookie_service import CookieManager
from database import db_execute, get_config, db_fetchall

router = APIRouter()
security = HTTPBearer(auto_error=False)


# ── Auth 中间件 ───────────────────────────────────────────────

async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    request: Request = None,
):
    require_auth = await get_config("openai_require_auth", "true")
    if require_auth.lower() != "true":
        return True

    configured_key = await get_config("openai_api_key", "sk-claude-proxy-default")
    provided_key = None

    if credentials:
        provided_key = credentials.credentials
    elif request:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_key = auth_header[7:]
        # 也支持 Query 参数传 api_key
        provided_key = provided_key or request.query_params.get("api_key")

    if provided_key != configured_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Invalid API key. Configure your key via /api/config/set (key=openai_api_key).",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )
    return True


# ── Pydantic 模型（OpenAI 格式）──────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = "claude-sonnet-4-6"
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stop: Optional[object] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    user: Optional[str] = None
    # 扩展字段
    tools_enabled: Optional[bool] = True
    account_id: Optional[int] = None  # 不传则自动轮询


# ── 模型格式转换工具 ──────────────────────────────────────────

def _openai_model_object(model_id: str, display_name: str = "") -> dict:
    return {
        "id": model_id,
        "object": "model",
        "created": 1700000000,
        "owned_by": "anthropic",
        "display_name": display_name,
    }


def _messages_to_prompt(messages: List[ChatMessage]) -> tuple[str, Optional[str]]:
    """
    将 OpenAI messages 列表转换为 Claude prompt 文本。
    system 消息提取为 system_prompt，其余拼接为对话格式。
    返回 (prompt, system_prompt)
    """
    system_parts = []
    dialog_parts = []

    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        elif msg.role == "user":
            dialog_parts.append(f"Human: {msg.content}")
        elif msg.role == "assistant":
            dialog_parts.append(f"Assistant: {msg.content}")
        elif msg.role == "tool":
            dialog_parts.append(f"Tool: {msg.content}")

    # 最后一条 user 消息作为 prompt
    prompt = dialog_parts[-1].replace("Human: ", "", 1) if dialog_parts else ""

    # 如果有多轮对话历史，将其拼入 prompt 前缀
    if len(dialog_parts) > 1:
        history = "\n".join(dialog_parts[:-1])
        prompt = f"{history}\n\nHuman: {prompt}"

    system_prompt = "\n\n".join(system_parts) if system_parts else None
    return prompt, system_prompt


def _chunk_to_openai_delta(chunk_data: dict, model: str, finish_reason=None) -> dict:
    """将 Claude SSE chunk 转换为 OpenAI SSE chunk 格式"""
    content = ""
    if chunk_data.get("type") == "content_block_delta":
        delta = chunk_data.get("delta", {})
        if delta.get("type") == "text_delta":
            content = delta.get("text", "")

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": content} if content else (
                    {"finish_reason": finish_reason} if finish_reason else {}
                ),
                "finish_reason": finish_reason,
            }
        ],
    }


# ── GET /v1/models ────────────────────────────────────────────

@router.get("/models", summary="列出可用模型（OpenAI 格式）")
async def list_models(auth=Depends(verify_api_key)):
    models = await ClaudeAPIService.list_models()
    data = [_openai_model_object(m["model_id"], m["display_name"]) for m in models]
    return {"object": "list", "data": data}


# ── POST /v1/chat/completions ─────────────────────────────────

@router.post("/chat/completions", summary="对话补全（OpenAI 格式）")
async def chat_completions(req: ChatCompletionRequest, request: Request,
                           auth=Depends(verify_api_key)):
    if not req.messages:
        raise HTTPException(400, detail={
            "error": {"message": "messages 不能为空", "type": "invalid_request_error"}
        })

    # 选择账号
    if req.account_id:
        account = await AccountManager.get_account(req.account_id)
        if not account or not account["is_active"]:
            raise HTTPException(400, detail={
                "error": {"message": f"账号 {req.account_id} 不存在或已禁用",
                          "type": "invalid_request_error"}
            })
    else:
        account = await AccountManager.pick_account()

    if not account:
        raise HTTPException(503, detail={
            "error": {
                "message": "当前没有可用账号，请检查 Cookie 配置。",
                "type": "service_unavailable",
            }
        })

    account_id = account["id"]
    org_uuid = await ClaudeAPIService.get_org_uuid(account_id)
    if not org_uuid:
        raise HTTPException(400, detail={
            "error": {
                "message": "无法获取 organization_uuid，请在账号中配置或确保 lastActiveOrg Cookie 存在。",
                "type": "invalid_request_error",
            }
        })

    # 转换消息格式
    prompt, system_prompt = _messages_to_prompt(req.messages)
    model = req.model or "claude-sonnet-4-6"

    # 创建/复用对话
    conv_uuid = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    await db_execute(
        """INSERT OR IGNORE INTO conversations
           (uuid, title, model, parent_message_uuid, organization_uuid, account_id, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (conv_uuid, prompt[:30], model,
         "00000000-0000-0000-0000-000000000000",
         org_uuid, account_id, now, now)
    )

    # ── 流式响应 ──────────────────────────────────────────────
    if req.stream:
        async def openai_stream():
            full_text = ""
            completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

            async for chunk in ClaudeAPIService.completion_stream(
                org_uuid=org_uuid, conv_uuid=conv_uuid, prompt=prompt,
                model=model, account_id=account_id,
                tools_enabled=req.tools_enabled,
                system_prompt=system_prompt,
            ):
                if not chunk.startswith("data:"):
                    continue
                raw = chunk[5:].strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue

                # 转发错误
                if data.get("type") == "error":
                    err_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
                        "error": {"message": data.get("message", "未知错误")},
                    }
                    yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n"
                    break

                # 文本增量
                if data.get("type") == "content_block_delta":
                    delta = data.get("delta", {})
                    text = delta.get("text", "") if delta.get("type") == "text_delta" else ""
                    if text:
                        full_text += text
                        sse_chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant", "content": text},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(sse_chunk, ensure_ascii=False)}\n\n"

                # 结束标志
                elif data.get("type") == "turn_complete":
                    finish_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(finish_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"

        return StreamingResponse(
            openai_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ── 非流式响应（聚合所有内容后返回）─────────────────────
    else:
        full_text = ""
        has_error = None

        async for chunk in ClaudeAPIService.completion_stream(
            org_uuid=org_uuid, conv_uuid=conv_uuid, prompt=prompt,
            model=model, account_id=account_id,
            tools_enabled=req.tools_enabled,
            system_prompt=system_prompt,
        ):
            if not chunk.startswith("data:"):
                continue
            raw = chunk[5:].strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            if data.get("type") == "error":
                has_error = data.get("message", "上游返回错误")
                break

            if data.get("type") == "content_block_delta":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta":
                    full_text += delta.get("text", "")

        if has_error:
            raise HTTPException(502, detail={
                "error": {"message": has_error, "type": "upstream_error"}
            })

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        prompt_tokens = len(prompt.split()) * 2      # 粗略估算
        completion_tokens = len(full_text.split()) * 2

        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        })


# ── GET /v1/models/{model_id} ─────────────────────────────────

@router.get("/models/{model_id}", summary="获取单个模型信息")
async def get_model(model_id: str, auth=Depends(verify_api_key)):
    rows = await db_fetchall(
        "SELECT * FROM models WHERE model_id=? AND is_active=1", (model_id,)
    )
    if not rows:
        raise HTTPException(404, detail={
            "error": {"message": f"模型 {model_id} 不存在", "type": "invalid_request_error"}
        })
    m = rows[0]
    return _openai_model_object(m["model_id"], m["display_name"])
