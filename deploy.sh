#!/usr/bin/env bash
# =============================================================================
# Claude 第三方管理客户端 - Linux 一键部署脚本 v2.0
# 支持：Ubuntu 20.04+ / Debian 11+ / CentOS 8+ / RHEL 8+
# 用法：chmod +x deploy.sh && sudo ./deploy.sh [--mode docker|native]
# =============================================================================
set -euo pipefail

# ── 颜色输出 ─────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()    { echo -e "\n${CYAN}══════════════════════════════════════════${NC}"; \
            echo -e "${CYAN}  $*${NC}"; \
            echo -e "${CYAN}══════════════════════════════════════════${NC}"; }

# ── 参数解析 ─────────────────────────────────────────────────
DEPLOY_MODE="docker"   # docker | native
PORT=8765
APP_DIR="/opt/claude-client"
SERVICE_USER="claude"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) DEPLOY_MODE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --dir)  APP_DIR="$2"; shift 2 ;;
    *) warn "未知参数: $1"; shift ;;
  esac
done

# ── 检测系统 ─────────────────────────────────────────────────
detect_os() {
  if   [[ -f /etc/debian_version ]]; then OS="debian"
  elif [[ -f /etc/redhat-release ]]; then OS="rhel"
  elif [[ -f /etc/arch-release    ]]; then OS="arch"
  else error "不支持的操作系统"; fi
  info "检测到系统: $OS"
}

# ── 安装系统依赖 ──────────────────────────────────────────────
install_deps() {
  step "安装系统依赖"
  case "$OS" in
    debian)
      apt-get update -qq
      apt-get install -y --no-install-recommends \
        curl wget git python3 python3-pip python3-venv \
        openssl ca-certificates gnupg lsb-release
      ;;
    rhel)
      yum install -y curl wget git python3 python3-pip openssl ca-certificates
      ;;
    arch)
      pacman -Sy --noconfirm curl wget git python python-pip openssl
      ;;
  esac
  success "系统依赖安装完成"
}

# ── 安装 Docker ───────────────────────────────────────────────
install_docker() {
  if command -v docker &>/dev/null; then
    success "Docker 已安装: $(docker --version)"
    return
  fi
  step "安装 Docker"
  case "$OS" in
    debian)
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update -qq
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
      ;;
    rhel)
      yum install -y yum-utils
      yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
      ;;
  esac
  systemctl enable --now docker
  success "Docker 安装完成"
}

# ── 部署（Docker 模式）───────────────────────────────────────
deploy_docker() {
  step "Docker 模式部署"

  # 创建应用目录
  mkdir -p "$APP_DIR"/{data,nginx/ssl,frontend}
  cd "$APP_DIR"

  # 生成自签名 SSL 证书（生产环境请替换为 Let's Encrypt）
  if [[ ! -f nginx/ssl/cert.pem ]]; then
    info "生成自签名 SSL 证书..."
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
      -keyout nginx/ssl/key.pem \
      -out  nginx/ssl/cert.pem \
      -subj "/C=CN/ST=Beijing/L=Beijing/O=Claude-Client/CN=localhost" 2>/dev/null
    success "SSL 证书已生成（有效期 10 年）"
    warn "生产环境请替换为 Let's Encrypt 证书：certbot certonly --standalone -d your-domain.com"
  fi

  # 复制代码文件（脚本与项目文件同目录时）
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -d "$SCRIPT_DIR/backend" ]]; then
    cp -r "$SCRIPT_DIR/backend"   "$APP_DIR/"
    cp -r "$SCRIPT_DIR/frontend"  "$APP_DIR/" 2>/dev/null || true
    cp    "$SCRIPT_DIR/requirements.txt" "$APP_DIR/"
    cp    "$SCRIPT_DIR/Dockerfile"       "$APP_DIR/"
    cp    "$SCRIPT_DIR/docker-compose.yml" "$APP_DIR/"
    cp    "$SCRIPT_DIR/nginx/nginx.conf" "$APP_DIR/nginx/"
    success "项目文件已复制到 $APP_DIR"
  fi

  # 构建并启动
  info "构建 Docker 镜像..."
  docker compose build --no-cache

  info "启动服务..."
  docker compose up -d

  success "Docker 部署完成"
}

# ── 部署（原生模式）─────────────────────────────────────────
deploy_native() {
  step "原生 Python 模式部署"

  # 检查 Python 版本
  PYTHON=$(command -v python3.11 || command -v python3.10 || command -v python3 || echo "")
  [[ -z "$PYTHON" ]] && error "未找到 Python 3.10+，请先安装"
  PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  info "Python 版本: $PY_VER"
  [[ "${PY_VER%%.*}" -lt 3 || ( "${PY_VER%%.*}" -eq 3 && "${PY_VER##*.}" -lt 10 ) ]] && \
    error "需要 Python 3.10+，当前版本: $PY_VER"

  # 创建应用目录
  mkdir -p "$APP_DIR"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [[ -d "$SCRIPT_DIR/backend" ]] && cp -r "$SCRIPT_DIR"/{backend,frontend,requirements.txt} "$APP_DIR/" 2>/dev/null || true
  cd "$APP_DIR"
  mkdir -p data

  # 创建虚拟环境
  info "创建 Python 虚拟环境..."
  $PYTHON -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip -q
  pip install -r requirements.txt -q
  success "Python 依赖安装完成"

  # 创建 systemd 服务
  info "配置 systemd 服务..."
  useradd -r -s /sbin/nologin "$SERVICE_USER" 2>/dev/null || true
  chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

  cat > /etc/systemd/system/claude-client.service << EOF
[Unit]
Description=Claude Third-Party Client Backend v2.0
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn backend.main:app \\
    --host 0.0.0.0 --port $PORT --workers 1 --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=TZ=Asia/Shanghai

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable claude-client
  systemctl restart claude-client
  success "systemd 服务已启动"

  # 安装并配置 nginx（可选）
  if command -v nginx &>/dev/null || apt-get install -y nginx &>/dev/null 2>&1; then
    mkdir -p /etc/nginx/ssl
    if [[ ! -f /etc/nginx/ssl/cert.pem ]]; then
      openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/nginx/ssl/key.pem \
        -out    /etc/nginx/ssl/cert.pem \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Claude-Client/CN=localhost" 2>/dev/null
    fi
    # 替换 nginx 配置中的路径
    sed "s|/usr/share/nginx/html|$APP_DIR/frontend|g; s|server backend:8765|server 127.0.0.1:$PORT|g" \
      "$SCRIPT_DIR/nginx/nginx.conf" > /etc/nginx/nginx.conf 2>/dev/null || true
    nginx -t && systemctl reload nginx
    success "Nginx 反向代理已配置"
  fi
}

# ── 安装后验证 ────────────────────────────────────────────────
verify_deployment() {
  step "验证部署"
  local max_wait=30
  local waited=0
  info "等待服务启动..."

  while [[ $waited -lt $max_wait ]]; do
    if curl -sf "http://localhost:$PORT/health" &>/dev/null; then
      break
    fi
    sleep 2
    waited=$((waited + 2))
  done

  if curl -sf "http://localhost:$PORT/health" &>/dev/null; then
    success "服务健康检查通过"
  else
    warn "服务可能尚未就绪，请稍等后手动检查: curl http://localhost:$PORT/health"
  fi
}

# ── 打印部署摘要 ──────────────────────────────────────────────
print_summary() {
  step "部署完成"
  echo ""
  echo -e "${GREEN}  ✅ Claude 第三方管理客户端 v2.0 部署成功！${NC}"
  echo ""
  echo -e "  管理界面       : ${CYAN}http://localhost:$PORT${NC}（或 https://your-domain）"
  echo -e "  API 文档        : ${CYAN}http://localhost:$PORT/docs${NC}"
  echo -e "  OpenAI 兼容层  : ${CYAN}http://localhost:$PORT/v1${NC}"
  echo ""
  echo -e "  ${YELLOW}接入说明（OpenAI 兼容）：${NC}"
  echo -e "    Base URL : http://your-server:$PORT/v1"
  echo -e "    API Key  : sk-claude-proxy-default（可在配置中修改）"
  echo -e "    支持客户端: ChatGPT Next Web / LobeChat / OpenWebUI / Cursor / 等"
  echo ""
  echo -e "  ${YELLOW}首次使用步骤：${NC}"
  echo -e "    1. 访问管理界面 → 认证配置 → 粘贴 Claude.ai Cookie"
  echo -e "    2. 在配置页面填入 org_uuid（或系统自动探测）"
  echo -e "    3. 测试连接 → 开始使用"
  echo ""
  if [[ "$DEPLOY_MODE" == "docker" ]]; then
    echo -e "  ${YELLOW}Docker 管理命令：${NC}"
    echo -e "    查看日志  : cd $APP_DIR && docker compose logs -f"
    echo -e "    重启服务  : cd $APP_DIR && docker compose restart"
    echo -e "    停止服务  : cd $APP_DIR && docker compose down"
    echo -e "    更新部署  : cd $APP_DIR && git pull && docker compose up -d --build"
  else
    echo -e "  ${YELLOW}服务管理命令：${NC}"
    echo -e "    查看状态  : systemctl status claude-client"
    echo -e "    查看日志  : journalctl -u claude-client -f"
    echo -e "    重启服务  : systemctl restart claude-client"
  fi
  echo ""
}

# ── 主流程 ────────────────────────────────────────────────────
main() {
  # 检查 root 权限
  [[ $EUID -ne 0 ]] && error "请以 root 权限运行: sudo ./deploy.sh"

  echo ""
  echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}║  Claude 第三方管理客户端 一键部署脚本 v2.0       ║${NC}"
  echo -e "${CYAN}║  部署模式: $DEPLOY_MODE | 端口: $PORT              ║${NC}"
  echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
  echo ""

  detect_os
  install_deps

  case "$DEPLOY_MODE" in
    docker)
      install_docker
      deploy_docker
      ;;
    native)
      deploy_native
      ;;
    *)
      error "未知部署模式: $DEPLOY_MODE（支持 docker | native）"
      ;;
  esac

  verify_deployment
  print_summary
}

main "$@"
