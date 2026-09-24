#!/usr/bin/env bash
#
# Обновление бота "БелыйОбходчик" на сервере: подтянуть код, зависимости, перезапустить.
# Запуск:  sudo bash /opt/beliy-obhodchik/deploy/update.sh
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/beliy-obhodchik}"
SERVICE_NAME="beliy-obhodchik"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "Нужны права root. Запустите: sudo bash $0" >&2
    exit 1
fi

echo "==> git pull"
git -C "$INSTALL_DIR" pull --ff-only

echo "==> pip install"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

echo "==> restart $SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
if [ -f /etc/systemd/system/beliy-obhodchik-support.service ]; then
    echo "==> restart beliy-obhodchik-support"
    systemctl restart beliy-obhodchik-support
fi
sleep 3
systemctl --no-pager --full status "$SERVICE_NAME" | head -n 15 || true
