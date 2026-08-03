#!/bin/bash
set -e

REPO_URL="https://github.com/hirogura/spw.git"
REPO_BRANCH="main"
INSTALL_DIR="/opt/lxd-data/spw"
SERVICE_NAME="spw"
PORT="${PORT:-3345}"
TAILSCALE_PORT=3344

echo "=== SPW Password Manager Installer (GitHub) ==="

# Check root
if [ "$EUID" -ne 0 ]; then
  echo "Error: Run as root (sudo bash $0)"
  exit 1
fi

# git確認
if ! command -v git &>/dev/null; then
  echo "Installing git..."
  if command -v apt-get &>/dev/null; then
    apt-get update -qq && apt-get install -y -qq git
  elif command -v yum &>/dev/null; then
    yum install -y git
  elif command -v dnf &>/dev/null; then
    dnf install -y git
  elif command -v pacman &>/dev/null; then
    pacman -Sy --noconfirm git
  else
    echo "Error: Cannot install git. Install manually."
    exit 1
  fi
fi
echo "git: $(git --version)"

# Python3確認
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 が見つかりません。インストールしてください。"
  exit 1
fi
echo "Python3: $(python3 --version)"

# Install 7zip if missing
if ! command -v 7z &>/dev/null; then
  echo "Installing 7zip..."
  if command -v apt-get &>/dev/null; then
    apt-get update -qq && apt-get install -y -qq p7zip-full
  elif command -v yum &>/dev/null; then
    yum install -y p7zip p7zip-plugins
  elif command -v dnf &>/dev/null; then
    dnf install -y p7zip p7zip-plugins
  elif command -v pacman &>/dev/null; then
    pacman -Sy --noconfirm p7zip
  else
    echo "Error: Cannot install 7zip. Install manually."
    exit 1
  fi
fi
echo "7zip: $(7z --help 2>&1 | head -1)"

# GitHubから取得・更新（data/ は .gitignore で除外済みのため保持される）
if [ -d "$INSTALL_DIR/.git" ]; then
  echo ""
  echo "=== 既存のインストールを GitHub から更新します ==="
  git -C "$INSTALL_DIR" fetch origin
  git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
elif [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  echo ""
  echo "=== 既存ファイルを検出しました。データを保持したまま再インストールします ==="
  if [ -d "$INSTALL_DIR/data" ]; then
    echo "data/ を一時退避中..."
    mv "$INSTALL_DIR/data" "/tmp/spw-data-backup-$$"
  fi
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
  if [ -d "/tmp/spw-data-backup-$$" ]; then
    echo "data/ を復元中..."
    mv "/tmp/spw-data-backup-$$" "$INSTALL_DIR/data"
  fi
else
  echo ""
  echo "=== 新規インストール: GitHub から取得します ==="
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# Create directories
mkdir -p "$INSTALL_DIR/data/backups"

# systemd service
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << SVCEOF
[Unit]
Description=SPW Password Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/server.py
Restart=on-failure
RestartSec=5
Environment=PORT=${PORT}

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

# Tailscale Serve設定
TAILSCALE_DOMAIN=""
echo ""
echo "Tailscale Serve を設定中..."
if ! command -v tailscale &>/dev/null; then
  echo "Warning: tailscale コマンドが見つかりません。スキップします。"
else
  tailscale serve --bg --https=${TAILSCALE_PORT} http://127.0.0.1:${PORT}
  TAILSCALE_DOMAIN=$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "")
  if [ -n "$TAILSCALE_DOMAIN" ]; then
    echo "Tailscale Serve: https://${TAILSCALE_DOMAIN}:${TAILSCALE_PORT}"
  else
    echo "Warning: Tailscale ドメイン取得に失敗しました。"
  fi
fi

echo ""
echo "=== インストール完了 ==="
echo ""
if [ -n "$TAILSCALE_DOMAIN" ]; then
  echo "URL (HTTPS): https://${TAILSCALE_DOMAIN}:${TAILSCALE_PORT}"
fi
echo "URL (ローカル): http://$(hostname -I | awk '{print $1}'):${PORT}"
echo ""
echo "コマンド:"
echo "  systemctl status  ${SERVICE_NAME}   # 状態確認"
echo "  systemctl restart ${SERVICE_NAME}   # 再起動"
echo "  journalctl -u ${SERVICE_NAME} -f    # ログ確認"
