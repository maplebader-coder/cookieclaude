"""
对话路由 /api/chat v2.0
新增：account_id 参数、多账号自动切换、模型选择
"""
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.claude_service import ClaudeAPIService
from services.account_service import AccountManager
from services.cookie_service import CookieManager
from database import db_execute, db_fetchall, db_fetchone, get_config

router = APIRouter()


def gen_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.utcnow().isoformat()


# ── Pydantic 模型 ─────────────────────────────────────────────

class CompletionRequest(BaseModel):
    prompt: str
    conversation_uuid: Optional[str] = None
    parent_message_uuid: Optional[str] = "00000000-0000-0000-0000-000000000000"
    model: Optional[str] = None  # None 时使用配置默认值
    locale: Optional[str] = None
    timezone: Optional[str] = None
    attachments: Optional[list] = []
    files: Optional[list] = []
    tools_enabled: Optional[bool] = True
    org_uuid: Optional[str] = None
    account_id: Optional[int] = None  # None 时自动轮询


class CreateConversationRequest(BaseModel):
    title: Optional[str] = "新建对话"
    model: Optional[str] = None
    org_uuid: Optional[str] = None
    account_id: Optional[int] = 1


# ── 会话管理 ──────────────────────────────────────────────────

@router.post("/conversations", summary="创建新对话")
async def create_conversation(req: CreateConversationRequest):
    conv_uuid = gen_uuid()
    model = req.model or await get_config("default_model", "claude-sonnet-4-6")
    account_id = req.account_id or 1

    org_uuid = req.org_uuid or await ClaudeAPIService.get_org_uuid(account_id)

    await db_execute(
        """INSERT INTO conversations (uuid,title,model,parent_message_uuid,organization_uuid,account_id,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (conv_uuid, req.title, model,
         "00000000-0000-0000-0000-000000000000", org_uuid, account_id, now_iso(), now_iso())
    )

    remote = {}
    if org_uuid:
        remote = await ClaudeAPIService.create_conversation(org_uuid, account_id)

    return {
        "success": True, "uuid": conv_uuid, "title": req.title,
        "model": model, "account_id": account_id, "remote": remote,
    }


@router.get("/conversations", summary="获取所有会话列表")
async def list_conversations(account_id: Optional[int] = None):
    if account_id:
        rows = await db_fetchall(
            "SELECT * FROM conversations WHERE account_id=? ORDER BY updated_at DESC LIMIT 100",
            (account_id,)
        )
    else:
        rows = await db_fetchall(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 100"
        )
    return {"conversations": rows, "total": len(rows)}


@router.get("/conversations/{conv_uuid}", summary="获取单个会话详情")
async def get_conversation(conv_uuid: str):
    conv = await db_fetchone("SELECT * FROM conversations WHERE uuid=?", (conv_uuid,))
    if not conv:
        raise HTTPException(404, "会话不存在")
    messages = await db_fetchall(
        "SELECT * FROM messages WHERE conversation_uuid=? ORDER BY created_at",
        (conv_uuid,)
    )
    return {"conversation": conv, "messages": messages}


@router.delete("/conversations/{conv_uuid}", summary="删除会话")
async def delete_conversation(conv_uuid: str):
    await db_execute("DELETE FROM messages WHERE conversation_uuid=?", (conv_uuid,))
    await db_execute("DELETE FROM conversations WHERE uuid=?", (conv_uuid,))
    return {"success": True}


@router.patch("/conversations/{conv_uuid}/title", summary="更新会话标题")
async def update_title(conv_uuid: str, data: dict):
    title = data.get("title", "")
    await db_execute(
        "UPDATE conversations SET title=?, updated_at=? WHERE uuid=?",
        (title, now_iso(), conv_uuid)
    )
    return {"success": True, "title": title}


# ── SSE 流式对话 ──────────────────────────────────────────────

@router.post("/completion", summary="SSE 流式对话（核心 /completion 代理）")
async def completion(req: CompletionRequest):
    # 选择账号（支持自动轮询）
    if req.account_id:
        account = await AccountManager.get_account(req.account_id)
        if not account:
            raise HTTPException(400, f"账号 {req.account_id} 不存在")
        account_id = req.account_id
    else:
        account = await AccountManager.pick_account()
        if not account:
            raise HTTPException(503, "无可用账号，请检查 Cookie 配置")
        account_id = account["id"]

    validation = await CookieManager.validate_required(account_id)
    if not validation["session_key_set"]:
        raise HTTPException(401, f"账号 {account_id} 的 sessionKey 未配置")

    org_uuid = req.org_uuid or await ClaudeAPIService.get_org_uuid(account_id)
    if not org_uuid:
        raise HTTPException(400, "缺少 organization_uuid，请配置账号或 lastActiveOrg Cookie")

    conv_uuid = req.conversation_uuid
    if not conv_uuid:
        conv_uuid = gen_uuid()
        model = req.model or await get_config("default_model", "claude-sonnet-4-6")
        await db_execute(
            """INSERT OR IGNORE INTO conversations
               (uuid,title,model,parent_message_uuid,organization_uuid,account_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (conv_uuid, "新建对话", model,
             "00000000-0000-0000-0000-000000000000", org_uuid, account_id, now_iso(), now_iso())
        )

    human_msg_uuid = gen_uuid()
    await db_execute(
        "INSERT INTO messages (uuid,conversation_uuid,role,content,parent_uuid,created_at) VALUES(?,?,?,?,?,?)",
        (human_msg_uuid, conv_uuid, "user", req.prompt, req.parent_message_uuid, now_iso())
    )

    model = req.model or await get_config("default_model", "claude-sonnet-4-6")
    locale = req.locale or await get_config("default_locale", "zh-CN")
    timezone = req.timezone or await get_config("default_timezone", "Asia/Shanghai")

    async def event_generator():
        full_response = ""
        assistant_uuid = None

        async for chunk in ClaudeAPIService.completion_stream(
            org_uuid=org_uuid, conv_uuid=conv_uuid, prompt=req.prompt,
            model=model, parent_message_uuid=req.parent_message_uuid,
            locale=locale, timezone=timezone,
            attachments=req.attachments, files=req.files,
            tools_enabled=req.tools_enabled, account_id=account_id,
        ):
            yield chunk
            if chunk.startswith("data:"):
                try:
                    data = json.loads(chunk[5:].strip())
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            full_response += delta.get("text", "")
                    elif data.get("type") == "turn_complete":
                        assistant_uuid = data.get("assistant_uuid")
                except Exception:
                    pass

        if full_response and assistant_uuid:
            await db_execute(
                "INSERT OR IGNORE INTO messages (uuid,conversation_uuid,role,content,parent_uuid,created_at) VALUES(?,?,?,?,?,?)",
                (assistant_uuid, conv_uuid, "assistant", full_response, human_msg_uuid, now_iso())
            )
            await db_execute(
                "UPDATE conversations SET parent_message_uuid=?, updated_at=? WHERE uuid=?",
                (assistant_uuid, now_iso(), conv_uuid)
            )

        # 自动生成标题（首次对话）
        msg_count = await db_fetchone(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_uuid=?", (conv_uuid,)
        )
        if msg_count and msg_count["cnt"] <= 2:
            title_result = await ClaudeAPIService.generate_title(org_uuid, conv_uuid, account_id)
            new_title = title_result.get("title", req.prompt[:20])
            await db_execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE uuid=?",
                (new_title, now_iso(), conv_uuid)
            )
            yield (f'data: {{"type":"title_update","title":"{new_title}",'
                   f'"conversation_uuid":"{conv_uuid}"}}\n\n')

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


# ── 文件上传 ──────────────────────────────────────────────────

@router.post("/upload", summary="上传文件到 claude.ai")
async def upload_file(
    file: UploadFile = File(...),
    conv_uuid: Optional[str] = None,
    org_uuid: Optional[str] = None,
    account_id: Optional[int] = 1,
):
    org_uuid = org_uuid or await ClaudeAPIService.get_org_uuid(account_id)
    content = await file.read()

    result = await ClaudeAPIService.upload_file(
        org_uuid=org_uuid, file_content=content,
        original_name=file.filename,
        content_type=file.content_type or "application/octet-stream",
        account_id=account_id,
    )

    if result.get("success"):
        await db_execute(
            """INSERT OR IGNORE INTO uploaded_files
               (file_uuid,file_name,sanitized_name,file_kind,size_bytes,conversation_uuid,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (result["file_uuid"], result["file_name"], result["sanitized_name"],
             result.get("file_kind", "image"), result.get("size_bytes", len(content)),
             conv_uuid, now_iso())
        )
    return result


@router.get("/files", summary="获取已上传文件列表")
async def list_files(conv_uuid: Optional[str] = None):
    if conv_uuid:
        rows = await db_fetchall(
            "SELECT * FROM uploaded_files WHERE conversation_uuid=? ORDER BY created_at DESC",
            (conv_uuid,)
        )
    else:
        rows = await db_fetchall("SELECT * FROM uploaded_files ORDER BY created_at DESC LIMIT 50")
    return {"files": rows, "total": len(rows)}


# ── 对话树 & 消息历史 ─────────────────────────────────────────

@router.get("/conversations/{conv_uuid}/tree", summary="获取对话树结构")
async def get_conversation_tree(conv_uuid: str, org_uuid: Optional[str] = None,
                                 account_id: Optional[int] = 1):
    org_uuid = org_uuid or await ClaudeAPIService.get_org_uuid(account_id)
    if not org_uuid:
        raise HTTPException(400, "需要 org_uuid")
    return await ClaudeAPIService.get_conversation_tree(org_uuid, conv_uuid, account_id)


@router.get("/messages/{conv_uuid}", summary="获取会话消息历史")
async def get_messages(conv_uuid: str):
    messages = await db_fetchall(
        "SELECT * FROM messages WHERE conversation_uuid=? ORDER BY created_at",
        (conv_uuid,)
    )
    return {"messages": messages, "total": len(messages)}


# ── 模型列表 ─────────────────────────────────────────────────

@router.get("/models", summary="获取可用模型列表")
async def list_models():
    models = await ClaudeAPIService.list_models()
    return {"models": models}
