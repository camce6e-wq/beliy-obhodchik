#!/usr/bin/env bash
#
# Автоустановка бота "БелыйОбходчик" на Linux VPS (Ubuntu/Debian).
#
# Быстрый запуск:
#   curl -fsSL https://raw.githubusercontent.com/camce6e-wq/beliy-obhodchik/main/deploy/install.sh -o /tmp/bo-install.sh
#   sudo bash /tmp/bo-install.sh
#
# Неинтерактивно (токены через переменные):
#   sudo TELEGRAM_BOT_TOKEN=xxx CRYPTOPAY_TOKEN=yyy bash /tmp/bo-install.sh
#
# Переменные (необязательно):
#   INSTALL_DIR=/opt/beliy-obhodchik  REPO_URL=https://github.com/camce6e-wq/beliy-obhodchik.git
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/beliy-obhodchik}"
REPO_URL="${REPO_URL:-https://github.com/camce6e-wq/beliy-obhodchik.git}"
SERVICE_NAME="beliy-obhodchik"
SERVICE_USER="beliy"
LOG_DIR="/var/log/${SERVICE_NAME}"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m!!!\033[0m %s\n' "$*" >&2; }

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    err "Нужны права root. Запустите: sudo bash $0"
    exit 1
fi

# --- Пакеты ---
log "Установка системных пакетов"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip git
else
    err "Поддерживаются только apt (Ubuntu/Debian) и dnf (Fedora)."
    exit 1
fi

# --- Пользователь сервиса ---
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    log "Создание системного пользователя $SERVICE_USER"
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER" || \
        adduser --system --home "/home/$SERVICE_USER" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# --- Код ---
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Обновление кода в $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
else
    log "Клонирование $REPO_URL -> $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# --- Виртуальное окружение ---
log "Создание venv и установка зависимостей"
python3 -m venv venv
./venv/bin/pip install --upgrade -q pip
./venv/bin/pip install -q -r requirements.txt

# --- Токены (.env) ---
if [ ! -f .env ]; then
    TK="${TELEGRAM_BOT_TOKEN:-}"
    CT="${CRYPTOPAY_TOKEN:-}"
    if [ -z "$TK" ] && [ -t 0 ]; then
        read -r -p "TELEGRAM_BOT_TOKEN (из @BotFather): " TK
    fi
    if [ -z "$CT" ] && [ -t 0 ]; then
        read -r -p "CRYPTOPAY_TOKEN (из @CryptoBot -> Crypto Pay): " CT
    fi
    if [ -z "$TK" ] || [ -z "$CT" ]; then
        cp .env.example .env
        err "Токены не заданы. Откройте $INSTALL_DIR/.env и заполните их, затем: systemctl restart $SERVICE_NAME"
    else
        cat > .env <<EOF
TELEGRAM_BOT_TOKEN=$TK
CRYPTOPAY_TOKEN=$CT
EOF
        log ".env создан"
    fi
fi
chmod 600 .env

# --- Логи и права ---
mkdir -p "$LOG_DIR"
touch "$LOG_DIR/bot.log"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$LOG_DIR"
chmod 600 .env

# --- systemd ---
log "Установка systemd-сервиса"
install -m 644 deploy/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true

if grep -q "ваш_токен" .env 2>/dev/null; then
    err "В .env остались плейсхолдеры — заполните токены и выполните: systemctl restart $SERVICE_NAME"
    exit 0
fi

systemctl restart "$SERVICE_NAME"
sleep 3
systemctl --no-pager --full status "$SERVICE_NAME" | head -n 15 || true

log "Готово. Логи: journalctl -u $SERVICE_NAME -f   или   tail -f $LOG_DIR/bot.log"
