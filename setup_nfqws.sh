#!/bin/sh
# =====================================================================
#  БелыйОбходчик — обход замедления/блокировок по DPI (Zapret / NFQWS)
#  Установка на Keenetic (Entware) или роутере с opkg/apk.
#
#  Запуск на роутере:
#      bash setup_nfqws.sh
#  (sudo не обязателен — скрипт сам определит права)
#
#  Что делает:
#    1. Проверяет Entware и opkg (при отсутствии — подскажет установку)
#    2. Ставит пакет nfqws-keenetic (методика Zapret, nfqws2)
#    3. Настраивает автозапуск S50nfqws
#    4. Запускает службу
# =====================================================================
set -u

# На Keenetic нет sudo, а пользователь — не root. Не падаем, а продолжаем:
# правами на /opt владеет admin, Entware-команды обычно выполняются прямо.
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        exec sudo sh "$0" "$@"
    else
        echo "==> Выполнение не от root и без sudo (обычно это Keenetic)."
        echo "    Если дальше появятся ошибки прав — выполните через SSH от root."
    fi
fi

if command -v nfqws >/dev/null 2>&1 || [ -x /opt/sbin/nfqws ] || [ -x /usr/sbin/nfqws ]; then
    echo "==> NFQWS уже установлен. Обновляю конфигурацию и перезапускаю."
    NFQWS_BIN="$(command -v nfqws || echo /opt/sbin/nfqws)"
    HAS_NFQWS=1
else
    HAS_NFQWS=0
fi

# --- 1. OPKG: ищем пакетный менеджер ---
OPKG=""
if command -v opkg >/dev/null 2>&1; then
    OPKG=opkg
elif [ -x /opt/bin/opkg ]; then
    OPKG=/opt/bin/opkg
fi

if [ -z "$OPKG" ]; then
    echo "=== Entware не найден ==="
    echo "Установите Entware на Keenetic (инструкция в разделе меню роутера"
    echo "«Пакеты Entware» -> «Установить», либо вручную с официального сайта"
    echo "entware.net). После установки Entware повторно запустите этот скрипт."
    exit 1
fi

# --- 2. Устанавливаем пакет nfqws-keenetic ---
if [ "$HAS_NFQWS" -eq 0 ]; then
    echo "==> Обновляю список пакетов ($OPKG update)..."
    "$OPKG" update || { echo "!!! Не удалось обновить списки пакетов (нет интернета?)." >&2; exit 1; }
    echo "==> Устанавливаю nfqws-keenetic..."
    "$OPKG" install nfqws-keenetic || {
        echo "Обычный пакет не найден, пробую 'nfqws'..." >&2
        "$OPKG" install nfqws || { echo "!!! Не удалось установить NFQWS. Пишите в поддержку: @beliy_obhodchik_support" >&2; exit 1; }
    }
fi

# --- 3. Конфигурация ---
# Файл конфигурации у nfqws-keenetic: /opt/etc/nfqws/nfqws.conf
CONF_DIR="/opt/etc/nfqws"
CONF_FILE="$CONF_DIR/nfqws.conf"

mkdir -p "$CONF_DIR"

# nfqws принимает --dpi-desync-fake-tls=<файл>; если файла нет — служба падает.
if [ ! -f "$CONF_DIR/dontneed" ]; then
    printf 'example.com\n' > "$CONF_DIR/dontneed"
fi

if [ -f "$CONF_FILE" ]; then
    echo "==> $CONF_FILE уже существует — не трогаем, только включаем автозапуск."
else
    echo "==> Создаю $CONF_FILE (базовая стратегия Zapret: разбивка сегментов)."
    cat > "$CONF_FILE" <<'EOF'
# БелыйОбходчик — базовая стратегия для РФ (Zapret).
# Разбивает исходящие TCP-пакеты на мелкие сегменты, чтобы DPI не мог
# распознать SNI по первому пакету. Покрывает YouTube, Discord, Instagram,
# TikTok и большинство сайтов, замедляемых по DPI.
NFQWS_OPT="--dpi-desync=fake,fakedsplit --dpi-desync-fooling=md5sig --dpi-desync-fake-tls=/opt/etc/nfqws/dontneed --dpi-desync-cutoff=d3 --dpi-desync-repeats=4"
EOF
fi

# --- 4. Автозапуск ---
INIT_DIR="/opt/etc/init.d"
INIT_SCRIPT="$INIT_DIR/S50nfqws"

if [ ! -f "$INIT_SCRIPT" ]; then
    echo "==> Создаю автозапуск $INIT_SCRIPT"
    # Пакет обычно ставит свой init-скрипт; на всякий случай создаём обёртку.
    if ! ls "$INIT_DIR"/S*[Nn][Ff][Qq][Ww][Ss]* >/dev/null 2>&1; then
        cat > "$INIT_SCRIPT" <<'EOF'
#!/bin/sh
START=50
start() {
    [ -x /opt/sbin/nfqws ] || return 0
    nfqws --daemon --pidfile=/var/run/nfqws.pid $(cat /opt/etc/nfqws/nfqws.conf 2>/dev/null | grep '^NFQWS_OPT=' | cut -d= -f2- | tr -d '"')
}
stop() {
    [ -f /var/run/nfqws.pid ] && kill "$(cat /var/run/nfqws.pid)" 2>/dev/null
}
restart() { stop; sleep 1; start; }
case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    *) echo "Usage: $0 {start|stop|restart}"; exit 1 ;;
esac
EOF
        chmod +x "$INIT_SCRIPT"
    fi
fi

# --- 5. Запускаем ---
echo "==> Запускаю NFQWS..."
if [ -x /opt/etc/init.d/S50nfqws ]; then
    /opt/etc/init.d/S50nfqws restart
elif [ -x /opt/sbin/nfqws ]; then
    /opt/sbin/nfqws --daemon --pidfile=/var/run/nfqws.pid $(grep '^NFQWS_OPT=' "$CONF_FILE" | cut -d= -f2- | tr -d '"')
fi

sleep 1
if pgrep -f nfqws >/dev/null 2>&1 || [ -s /var/run/nfqws.pid ]; then
    echo ""
    echo "=============================================="
    echo "  ГОТОВО! NFQWS работает и включён в автозапуск."
    echo "  Проверьте YouTube / Discord / Instagram / TikTok."
    echo "=============================================="
    exit 0
else
    echo "!!! Не удалось запустить NFQWS. Проверьте вывод выше и напишите в поддержку: @beliy_obhodchik_support" >&2
    exit 1
fi