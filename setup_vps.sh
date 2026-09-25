#!/usr/bin/env bash
#
# Установка VLESS Reality (Xray) на VPS пользователя для «БелыйОбходчик».
#
# Запуск (от root):
#   sudo bash setup_vps.sh <sni_hostname> [sni_ip]
# или через переменные окружения:
#   SNI_HOSTNAME=api.notion.com [SNI_IP=1.2.3.4] sudo bash setup_vps.sh
#
# После успешной установки печатает блок параметров:
#   ===BELIY-OBHODCHIK-VLESS===
#   SERVER_IP=...
#   UUID=...
#   PUBLIC_KEY=...
#   SHORT_ID=...
#   SNI_HOSTNAME=...
#   SNI_IP=...
#   ===BELIY-OBHODCHIK-VLESS-END===
# Скопируйте весь блок и отправьте боту целиком.
#
set -euo pipefail

SNI_HOSTNAME="${1:-${SNI_HOSTNAME:-}}"
SNI_IP="${2:-${SNI_IP:-}}"

die() { echo "❌ $*" >&2; exit 1; }

[ -n "$SNI_HOSTNAME" ] || die "Использование: sudo bash setup_vps.sh <sni_hostname> [sni_ip]"
[ "$(id -u)" = "0" ] || die "Нужны права root. Запустите через sudo."

# 1. Устанавливаем Xray, если его нет
if ! command -v xray >/dev/null 2>&1; then
    echo "==> Устанавливаю Xray-core..."
    bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
fi
command -v xray >/dev/null 2>&1 || die "Не удалось установить Xray."

# 2. Генерируем настоящие ключи и параметры
echo "==> Генерирую ключи Reality..."
UUID="$(command -v uuidgen >/dev/null 2>&1 && uuidgen || cat /proc/sys/kernel/random/uuid)"
SHORT_ID="$(openssl rand -hex 4 2>/dev/null || cat /proc/sys/kernel/random/uuid | cut -d- -f1)"
KEYS="$(xray x25519)"
# Подписи ключей менялись между версиями xray: "Private key:"/"PrivateKey:",
# "Public key:"/"PublicKey", а в части сборок публичный ключ печатается как
# "Password:". Берём значение после двоеточия по любой из этих подписей.
PRIVATE_KEY="$(printf '%s\n' "$KEYS" | awk -F': *' '/^[Pp]rivate/{print $NF}' | tr -d '[:space:]')"
PUBLIC_KEY="$(printf '%s\n' "$KEYS" | awk -F': *' '/^(Public|Password)/{print $NF}' | tr -d '[:space:]')"
[ -n "$PRIVATE_KEY" ] && [ -n "$PUBLIC_KEY" ] || die "Не удалось получить ключи x25519 (неожиданный вывод xray): $KEYS"

DEST="${SNI_IP:+$SNI_IP:443}"
[ -n "$DEST" ] || DEST="${SNI_HOSTNAME}:443"

# 3. Пишем конфигурацию сервера
mkdir -p /usr/local/etc/xray
cat > /usr/local/etc/xray/config.json <<EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": 443,
    "protocol": "vless",
    "settings": {
      "clients": [{"id": "$UUID", "flow": "xtls-rprx-vision"}],
      "decryption": "none"
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "dest": "$DEST",
        "serverNames": ["$SNI_HOSTNAME"],
        "privateKey": "$PRIVATE_KEY",
        "shortIds": ["$SHORT_ID"],
        "minClientVer": "",
        "maxClientVer": "",
        "maxTimeDiff": 0,
        "fingerprint": "chrome"
      }
    },
    "sniffing": {"enabled": true, "destOverride": ["http", "tls"]}
  }],
  "outbounds": [
    {"protocol": "freedom", "tag": "direct"},
    {"protocol": "blackhole", "tag": "blocked"}
  ],
  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [{"type": "field", "outboundTag": "blocked", "protocol": ["bittorrent"]}]
  }
}
EOF

# 4. systemd-служба (если не создана официальным установщиком)
if [ ! -f /etc/systemd/system/xray.service ]; then
    cat > /etc/systemd/system/xray.service <<'SVC'
[Unit]
Description=Xray Service
After=network.target nss-lookup.target

[Service]
User=root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
NoNewPrivileges=true
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartPreventExitStatus=23
LimitNPROC=10000
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
SVC
fi

systemctl daemon-reload
systemctl enable xray >/dev/null 2>&1 || true
systemctl restart xray
systemctl is-active xray >/dev/null 2>&1 || die "Xray не запустился."

# 5. Фаервол: порт 443 (не критично при неудаче)
if command -v ufw >/dev/null 2>&1 && ufw status >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then
    ufw allow 443/tcp >/dev/null 2>&1 || true
elif command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -A INPUT -p tcp --dport 443 -j ACCEPT
    command -v iptables-save >/dev/null 2>&1 && iptables-save > /etc/iptables/rules.v4 || true
fi

# 6. Определяем публичный IP
SERVER_IP="$(curl -s -m 10 https://api.ipify.org 2>/dev/null || curl -s -m 10 ifconfig.me 2>/dev/null || true)"
[ -n "$SERVER_IP" ] || SERVER_IP="$SNI_IP"

# 7. Выводим блок параметров для бота
cat <<BLOCK

===BELIY-OBHODCHIK-VLESS===
SERVER_IP=$SERVER_IP
UUID=$UUID
PUBLIC_KEY=$PUBLIC_KEY
SHORT_ID=$SHORT_ID
SNI_HOSTNAME=$SNI_HOSTNAME
SNI_IP=$SNI_IP
===BELIY-OBHODCHIK-VLESS-END===

✅ Установка завершена! Скопируйте весь блок выше (от ===BELIY-OBHODCHIK-VLESS=== до ===BELIY-OBHODCHIK-VLESS-END===) и отправьте боту.
BLOCK