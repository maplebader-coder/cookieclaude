# ── 构建阶段 ─────────────────────────────────────────────────
FROM python:3.11-slim AS base

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制 requirements 以利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# 数据目录
RUN mkdir -p /app/data

# 非 root 用户运行
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

CMD ["python", "-m", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8765", \
     "--workers", "1", "--log-level", "info"]
