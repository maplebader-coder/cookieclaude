"""
Claude 第三方管理客户端 - 后端主入口 v2.0
新增：OpenAI 兼容层 /v1、多账号路由 /api/accounts、Cookie 自动刷新调度器
运行: cd backend && python main.py
"""
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import os, sys

sys.path.insert(0, os.path.dirname(__file__))

from routers import auth_router, chat_router, cookie_router, proxy_router, config_router
from routers import account_router, openai_router
from database import init_db
from services.cookie_service import CookieRefreshScheduler

app = FastAPI(
    title="Claude 第三方管理客户端 API",
    description=(
        "基于 Cookie 注入 + Google OAuth 的 Claude.ai 第三方接入层\n\n"
        "**OpenAI 兼容 API**：`/v1/chat/completions` 和 `/v1/models`，"
        "可直接接入任何支持 OpenAI 格式的客户端（如 ChatGPT Next Web、LobeChat、OpenWebUI 等）。"
    ),
    version="2.0.0",
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由注册 ──────────────────────────────────────────────────
app.include_router(auth_router.router,    prefix="/api/auth",     tags=["认证 Auth"])
app.include_router(chat_router.router,    prefix="/api/chat",     tags=["对话 Chat"])
app.include_router(cookie_router.router,  prefix="/api/cookie",   tags=["Cookie 管理"])
app.include_router(proxy_router.router,   prefix="/api/proxy",    tags=["代理 Proxy"])
app.include_router(config_router.router,  prefix="/api/config",   tags=["配置 Config"])
app.include_router(account_router.router, prefix="/api/accounts", tags=["多账号管理"])
# OpenAI 兼容层 —— 挂载到 /v1，方便客户端直接配置 base_url
app.include_router(openai_router.router,  prefix="/v1",           tags=["OpenAI 兼容层 /v1"])

# ── 前端静态文件 ──────────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ── 启动事件 ──────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    await init_db()
    await CookieRefreshScheduler.start()
    print("✅ 数据库初始化完成")
    print("🚀 Claude 第三方客户端后端 v2.0 启动")
    print("   管理界面      : http://localhost:8765")
    print("   API 文档      : http://localhost:8765/docs")
    print("   OpenAI 兼容层 : http://localhost:8765/v1")


@app.on_event("shutdown")
async def on_shutdown():
    await CookieRefreshScheduler.stop()


# ── 静态页面 ──────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"status": "Claude 第三方客户端 v2.0 运行中", "api_docs": "/docs",
                         "openai_compat": "/v1"})


@app.get("/health", summary="健康检查")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ── 启动入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8765,
        reload=True,
        log_level="info",
    )
