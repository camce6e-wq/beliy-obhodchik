#!/usr/bin/env python3
"""
Telegram-бот для автоматической продажи конфигов
Полностью автономная обработка заказов
"""

import os
import sys
import re
import time
import logging
import hashlib
import threading
import ipaddress
import shlex
from datetime import datetime
from typing import Optional, Any
import sqlite3
import secrets

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Для работы с Telegram API
try:
    import telebot
    from telebot import types
except ImportError:
    print("Установите библиотеку: pip install pyTelegramBotAPI")
    exit(1)

try:
    import requests
except ImportError:
    print("Установите библиотеку: pip install requests")
    exit(1)

# Импортируем наши модули
from sni_manager import SNIDatabase, SNIScanner
from keenetic_config_generator import KeeneticConfigGenerator, quick_generate

# SSH-установка VPS (paramiko)
try:
    import paramiko
except ImportError:
    paramiko = None

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _load_dotenv(path: str = ".env") -> None:
    """Мини-загрузчик .env без внешних зависимостей.
    Не перезаписывает уже заданные переменные (нужно для systemd EnvironmentFile)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(("#", ";")) or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except (FileNotFoundError, PermissionError):
        pass
    except OSError as e:
        print(f"Не удалось прочитать {path}: {e}", file=sys.stderr)


_load_dotenv()

# Токены берём из окружения (.env). Плейсхолдер ниже — НЕ настоящий секрет,
# он нужен только для офлайн-тестов; продакшен берёт токены из .env
# (автоматически при запуске, или через bat/systemd).
TELEGRAM_BOT_TOKEN = os.environ.get(
    'TELEGRAM_BOT_TOKEN',
    "0000000000:AA0000000000000000000000000000000000"
)
CRYPTOPAY_TOKEN = os.environ.get('CRYPTOPAY_TOKEN', "")

# Рекомендуемые VPS-площадки — для монетизации через партнёрские ссылки.
# url="" — площадка показывается без ссылки.
VPS_RECOMMENDATIONS = [
    ("AdminVPS", "Россия/Европа", "от 150 ₽/месяц", "https://my.adminvps.ru/aff.php?aff=32386"),
]

# Устройства, на которые можно выдать готовые настройки (расширяемый список).
SUPPORTED_DEVICES = [
    "Keenetic (все модели со встроенным VPN)",
    "ASUS (модели с VPN-клиентом)",
    "Роутеры на OpenWrt",
    "Xiaomi / MI Router",
    "TP-Link (модели с VPN-клиентом)",
    "Android-телефон (приложение v2rayNG)",
    "iPhone (приложение Streisand)",
    "Компьютер Windows / macOS / Linux (приложение)",
]

# Дополнительные услуги, которые можно заказать через поддержку (расширяемый список).
ADDITIONAL_SERVICES = [
    ("Claude Code", "установка и настройка за вас на вашем сервере"),
    ("MT4 / MT5 и торговые терминалы", "стабильный доступ к зарубежным серверам"),
    ("Установка Xray на несколько серверов", "резервные каналы под рукой"),
    ("Перенос настроек с одного роутера на другой", "сменили роутер — всё переехало"),
]

# ===== Каталог роутеров для подбора =====
# Ключ callback -> (название категории, описание, список моделей)
# Каждая модель: (имя, цена, фичи, годный_ли_VPN)
ROUTER_CATEGORIES = {
    "budget": (
        "Бюджетный роутер",
        "Для квартиры-студии или 1-2 комнат: базовый интернет, телефон и ноутбук.",
        [
            ("TP-Link Archer AX55", "~4 000 ₽", "Wi-Fi 6, до 2400 Мбит/с, VPN-клиент есть"),
            ("Xiaomi Redmi Router AX3000", "~3 000 ₽", "Wi-Fi 6, быстрый, VPN-клиент через OpenWrt"),
        ]
    ),
    "mid": (
        "Средний роутер",
        "Для дома до 100 м² или 3-4 комнат: стабильный VPN, много устройств.",
        [
            ("Keenetic Giga", "~8 000 ₽", "Wi-Fi 5+6, без проблем с Xray, родная поддержка VLESS Reality"),
            ("Keenetic Hopper", "~9 000 ₽", "Wi-Fi 6, мощный, для тяжёлых задач"),
            ("ASUS RT-AX55", "~6 000 ₽", "Wi-Fi 6, встроенный VPN-клиент"),
        ]
    ),
    "premium": (
        "Мощный роутер",
        "Для дома со многими устройствами, игр и 4K-видео, площадь любая.",
        [
            ("Keenetic Ultra", "~15 000 ₽", "Флагман, WiFi 6E, топовый VPN"),
            ("ASUS ROG Rapture / RT-AX86U", "~20 000 ₽", "Для требовательных пользователей"),
        ]
    ),
    "keenetic_family": (
        "Уже есть Keenetic?",
        "Роутеры Keenetic — наши любимые: на них настройка максимально автоматическая.",
        [
            ("Любая модель Giga/Ultra/Hopper/Omni", "от 3 000 ₽ (б/у)", "Просто скажите, какая у вас модель"),
        ]
    ),
}

# Роутеры, с которыми работаем «кнопкой в один клик» (авто-генерация готового ZIP)
SUPPORTED_ROUTER_MODELS = [
    "Keenetic Giga",
    "Keenetic Ultra",
    "Keenetic Hopper",
    "Keenetic Omni",
]

# Промокод на скидку для рекомендуемых площадок (пусто = не показывать).
PROMO_CODE = "BELOBH"

# Оплата звёздами Telegram: цена в звёздах за настройку.
STARS_PRICE = 750

# VPS-настройка: цена в рублях через Crypto Bot.
VPS_RUB_PRICE = 1500

# ===== Услуга «Без сервера» (NFQWS / Zapret / ByeDPI) =====
# Обход замедления и блокировок Ютуба, Дискорда, Инстаграма, ТикТока, Твиттера,
# многих сайтов — прямо на роутере/ПК/телефоне, БЕЗ покупки VPS-сервера.
# Важно: NFQWS/Zapret убирает блокировки, сделанные на уровне DPI (замедление/
# блокировка по SNI и сигнатуре). Если провайдер режет по IP-подсетям целиком —
# там уже нужен свой сервер (VPS). Мы честно это объясняем пользователю.
DPI_RUB_PRICE = 1000  # Через Crypto Bot
DPI_STARS_PRICE = 500 # Через звёзды Telegram (дешевле, т.к. сервера нет)
CALLBACK_COOLDOWN = float(os.environ.get('CALLBACK_COOLDOWN', '2'))
COMMAND_COOLDOWN = float(os.environ.get('COMMAND_COOLDOWN', '2'))
# ID админов/владельцев через запятую: ADMIN_USER_IDS=111,222,333
ADMIN_USER_IDS = [int(x) for x in os.environ.get('ADMIN_USER_IDS', '').split(',') if x.strip()]
# Максимум активных (неоплаченных) счетов на одного пользователя
MAX_PENDING_PER_USER = int(os.environ.get('MAX_PENDING_PER_USER', '1'))

# Оборот DPI нужен только за "замедляющей" сетью (домашний ПК, РФ).
# На нормальном VPS оставьте выключенным (по умолчанию): тогда используется
# обычный long polling с keep-alive.
#   DPI_WORKAROUND=1  -> короткие соединения (Connection: close) + short polling
#   DPI_WORKAROUND=0  -> нормальный режим для сервера (по умолчанию)
DPI_WORKAROUND = os.environ.get('DPI_WORKAROUND', '').strip().lower() in ('1', 'true', 'yes', 'on')

if DPI_WORKAROUND:
    import telebot.apihelper as _ah
    _orig_session = _ah._get_req_session

    def _fresh_session():
        s = _orig_session()
        s.headers['Connection'] = 'close'
        return s

    _ah._get_req_session = _fresh_session

# Значение getUpdates(timeout=...). При DPI-костыле 0 (короткие опросы), иначе 20.
LONG_POLLING_TIMEOUT = int(os.environ.get('LONG_POLLING_TIMEOUT', '0' if DPI_WORKAROUND else '20'))

# Фоновый пересмотр здоровья SNI-доноров (секунды). 0 -> выключено.
SNI_CHECK_INTERVAL = int(os.environ.get('SNI_CHECK_INTERVAL', '3600'))

class PaymentSystem:
    """Класс для обработки платежей (упрощённая версия)"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.environ.get('PAYMENTS_DB', 'payments.db')
        self.init_database()
    
    def _connect(self) -> sqlite3.Connection:
        """Соединение с SQLite с busy_timeout (защита от 'database is locked' при WAL)."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    
    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    
    def init_database(self):
        """Инициализация базы платежей"""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    username TEXT,
                    amount INTEGER,
                    currency TEXT DEFAULT 'RUB',
                    status TEXT DEFAULT 'pending',
                    source TEXT DEFAULT 'crypto',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    paid_at TIMESTAMP,
                    metadata TEXT
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    payment_id TEXT,
                    user_id INTEGER,
                    config_path TEXT,
                    server_ip TEXT,
                    uuid TEXT,
                    sni_hostname TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    delivered_at TIMESTAMP,
                    FOREIGN KEY (payment_id) REFERENCES payments(payment_id)
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id TEXT PRIMARY KEY,
                    payment_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    server_ip TEXT,
                    sni_hostname TEXT,
                    status TEXT DEFAULT 'active',
                    pay_url TEXT,
                    source TEXT DEFAULT 'crypto',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vps_setups (
                    user_id INTEGER PRIMARY KEY,
                    server_ip TEXT NOT NULL,
                    uuid TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    short_id TEXT NOT NULL,
                    sni_hostname TEXT NOT NULL,
                    sni_ip TEXT,
                    install_method TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Миграция уже существующих БД: добавляем недостающие колонки
            self._ensure_column(conn, "payments", "source", "TEXT DEFAULT 'crypto'")
            self._ensure_column(conn, "invoices", "source", "TEXT DEFAULT 'crypto'")
            conn.commit()
    
    def create_payment(self, user_id: int, username: str, amount: int = VPS_RUB_PRICE,
                       payment_id: Optional[str] = None, source: str = "crypto") -> str:
        """Создание нового платежа"""
        if payment_id is None:
            payment_id = f"pay_{hashlib.md5(f'{user_id}{datetime.now()}{secrets.token_hex(4)}'.encode()).hexdigest()[:16]}"
        
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO payments (payment_id, user_id, username, amount, status, source)
                VALUES (?, ?, ?, ?, 'pending', ?)
            """, (payment_id, user_id, username, amount, source))
            
            conn.commit()
        
        logger.info(f"Создан платёж {payment_id} для пользователя {username} ({user_id})")
        return payment_id
    
    def confirm_payment(self, payment_id: str):
        """Подтверждение оплаты"""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE payments 
                SET status = 'paid', paid_at = CURRENT_TIMESTAMP
                WHERE payment_id = ?
            """, (payment_id,))
            
            conn.commit()
        
        logger.info(f"Платёж {payment_id} подтверждён")
    
    def get_payment(self, payment_id: str) -> Optional[dict]:
        """Получение платежа по payment_id (для сверки суммы в момент доставки)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
            ).fetchone()
            return dict(row) if row else None
    
    def mark_payment_stars(self, payment_id: str, amount_stars: int):
        """Переход оплаты на звёзды: фиксируем цену в звёздах и источник."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE payments SET amount = ?, currency = 'XTR', source = 'stars' WHERE payment_id = ?",
                (amount_stars, payment_id),
            )
            conn.commit()
    
    def create_order(self, payment_id: str, user_id: int, 
                     config_path: str, server_ip: str, 
                     uuid: str, sni_hostname: str) -> str:
        """Создание заказа. Идемпотентно: для одного платежа один заказ
        (повторная выдача/сбой не плодит дубликаты)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT order_id FROM orders WHERE payment_id = ? LIMIT 1", (payment_id,)
            ).fetchone()
            if row:
                return row[0]
            order_id = f"order_{hashlib.md5(f'{payment_id}{datetime.now()}'.encode()).hexdigest()[:12]}"
            conn.execute("""
                INSERT INTO orders (order_id, payment_id, user_id, config_path, server_ip, uuid, sni_hostname)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (order_id, payment_id, user_id, config_path, server_ip, uuid, sni_hostname))
            conn.commit()
        
        logger.info(f"Создан заказ {order_id} для платежа {payment_id}")
        return order_id
    
    def find_paid_order(self, payment_id: str) -> Optional[dict]:
        """Заказ, для которого конфиг уже доставлен (идемпотентность выдачи)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM orders WHERE payment_id = ? AND delivered_at IS NOT NULL LIMIT 1",
                (payment_id,),
            ).fetchone()
            return dict(row) if row else None
    
    def get_user_orders(self, user_id: int) -> list:
        """Получение заказов пользователя"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT o.*, p.status as payment_status
                FROM orders o
                JOIN payments p ON o.payment_id = p.payment_id
                WHERE o.user_id = ?
                ORDER BY o.created_at DESC
            """, (user_id,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def mark_order_delivered(self, order_id: str):
        """Отметка заказа как доставленного"""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE orders 
                SET delivered_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            """, (order_id,))
            
            conn.commit()
        
        logger.info(f"Заказ {order_id} отмечен как доставленный")

    def save_vps_setup(self, user_id: int, server_ip: str, uuid: str,
                       public_key: str, short_id: str,
                       sni_hostname: str, sni_ip: str = None,
                       install_method: str = "self") -> None:
        """Сохранение реально установленной VPS-конфигурации (ключи x25519 с VPS)."""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO vps_setups
                (user_id, server_ip, uuid, public_key, short_id,
                 sni_hostname, sni_ip, install_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, server_ip, uuid, public_key, short_id,
                  sni_hostname, sni_ip, install_method))
            conn.commit()
        logger.info(f"Сохранены реальные ключи VPS для пользователя {user_id}")

    def get_vps_setup(self, user_id: int) -> Optional[dict]:
        """Получение реально установленных ключей VLESS Reality для пользователя."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM vps_setups WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_order(self, order_id: str) -> Optional[dict]:
        """Получение одного заказа по order_id."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_vps_setup_sni(self, user_id: int, sni_hostname: str,
                             sni_ip: Optional[str] = None) -> bool:
        """Смена SNI-донора в сохранённой установке (ключи VPS не меняются)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE vps_setups SET sni_hostname = ?, sni_ip = ? WHERE user_id = ?",
                (sni_hostname, sni_ip, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def create_invoice_record(self, invoice_id: str, payment_id: str, user_id: int,
                              chat_id: int, server_ip: Optional[str],
                              sni_hostname: Optional[str], pay_url: str,
                              source: str = "crypto"):
        """Сохраняем созданный счёт (для проверки оплаты после перезапуска)"""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO invoices
                (invoice_id, payment_id, user_id, chat_id, server_ip, sni_hostname, status, pay_url, source)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """, (invoice_id, payment_id, user_id, chat_id, server_ip, sni_hostname, pay_url, source))
            conn.commit()

    def get_active_invoices(self) -> dict:
        """Все ожидающие оплаты счета: payment_id -> данные"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM invoices WHERE status = 'active'
            """).fetchall()
            return {r['payment_id']: dict(r) for r in rows}

    def get_invoice_by_payment(self, payment_id: str) -> Optional[dict]:
        """Счёт по payment_id (для восстановления и проверки после перезапуска)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM invoices WHERE payment_id = ? LIMIT 1", (payment_id,)
            ).fetchone()
            return dict(row) if row else None

    def mark_invoice_paid(self, invoice_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE invoices SET status = 'paid' WHERE invoice_id = ?", (invoice_id,))
            conn.commit()
            return cur.rowcount > 0


class CryptoPayClient:
    """Клиент Crypto Pay (Crypto Bot) для создания и проверки счетов."""

    BASE = "https://pay.crypt.bot/api"

    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            'Crypto-Pay-API-Token': token,
            'Content-Type': 'application/json',
        })

    def _post(self, method: str, payload: Optional[dict] = None) -> Any:
        r = self.session.post(f"{self.BASE}/{method}", json=payload or {}, timeout=15)
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"CryptoPay HTTP {r.status_code}: {r.text[:200]}")
        if not j.get('ok'):
            raise RuntimeError(str(j.get('error', j))[:300])
        return j.get('result')

    def create_invoice(self, amount_rub: int = VPS_RUB_PRICE, payload: str = "",
                       description: str = None) -> dict:
        """Счёт на фиксированную сумму в рублях (конвертируется в USDT)."""
        return self._post('createInvoice', {
            'asset': 'USDT',
            'amount': amount_rub,
            'currency_type': 'fiat',
            'fiat': 'RUB',
            'description': description or "БелыйОбходчик — автоматическая настройка Keenetic",
            'payload': payload,
            'allow_anonymous': True,
            'paid_btn_name': 'viewItem',
            'paid_btn_url': 'https://camce6e-wq.github.io/beliy-obhodchik/',
        })

    def get_paid_invoices(self) -> list:
        """Все оплаченные счета (с пагинацией по 100 и без дубликатов invoice_id)."""
        items = []
        seen = set()
        offset = 0
        while True:
            res = self._post('getInvoices', {'status': 'paid', 'count': 100, 'offset': offset})
            batch = res.get('items') if isinstance(res, dict) else (res or [])
            if not batch:
                break
            for inv in batch:
                iid = inv.get('invoice_id')
                if iid not in seen:
                    seen.add(iid)
                    items.append(inv)
            if len(batch) < 100:
                break
            offset += len(batch)
        return items


class _CallbackMessage:
    """Лёгкий адаптер callback-нажатия под вид message.
    Позволяет inline-кнопкам главного меню переиспользовать функции-обработчики команд
    (buy/dpi/router/vps/guide/faq/support/myorders), которые ожидают message."""

    def __init__(self, call):
        self.chat = getattr(call.message, 'chat', None)
        self.from_user = call.from_user
        self.text = (call.data or "")


class AutoConfigBot:
    """Основной класс Telegram-бота"""
    
    def __init__(self, token: str, crypto_pay_token: str = ""):
        self.bot = telebot.TeleBot(token)
        self.payment_system = PaymentSystem()
        self.sni_db = SNIDatabase()
        self.scanner = SNIScanner(self.sni_db)
        self.crypto_pay = CryptoPayClient(crypto_pay_token) if crypto_pay_token else None

        # Общая блокировка: защищаем user_data / pending_payments / support_forwards
        # от гонок между фоновым лупом оплат и хендлерами Telegram.
        self._lock = threading.RLock()
        # Неудачные доставки: pid -> время, чтобы не крутить одну ошибку каждые 10 секунд
        self._delivery_backoff = {}

        # Состояния пользователей (user_id -> state)
        self.user_states = {}

        # Данные пользователей (user_id -> data)
        self.user_data = {}

        # Карта пересланных сообщений поддержки: message_id у владельца -> (user_id, chat_id)
        self.support_forwards = {}

        # Ожидающие оплаты: payment_id -> {payment_id, user_id, chat_id, server_ip, sni_hostname}
        self.pending_payments = {}

        # Защита: антифлуд (user_id -> время) и сдержанные уведомления о флуде
        self.last_cmd = {}
        self._spam_notified = {}

        # Восстанавливаем ожидающие счета после перезапуска
        for pid, row in self.payment_system.get_active_invoices().items():
            self.pending_payments[pid] = {
                'payment_id': pid,
                'user_id': row['user_id'],
                'chat_id': row['chat_id'],
                'server_ip': row['server_ip'],
                'sni_hostname': row['sni_hostname'],
            }

        # Регистрация обработчиков
        self.register_handlers()

    # ===== Защита от спама и злоупотреблений =====

    def _is_private(self, chat_type) -> bool:
        return chat_type == 'private'

    def _guard_message(self, message) -> bool:
        """True, если сообщение можно обрабатывать (только личные чаты)."""
        chat_type = getattr(message.chat, 'type', None)
        if chat_type != 'private':
            try:
                self.bot.send_message(
                    message.chat.id,
                    "ℹ️ Я работаю только в личных сообщениях.\nОткройте @beliy_obhodchik_bot и пишите там.",
                )
            except Exception:
                pass
            return False
        return True

    def _guard_callback(self, call) -> bool:
        chat_type = getattr(getattr(call, 'message', None), 'chat', None)
        return self._is_private(getattr(chat_type, 'type', None)) if chat_type else False

    def _antiflood(self, user_id: int, cooldown: float = 2.0) -> bool:
        """True, если пользователь заблокирован антифлудом."""
        if user_id in ADMIN_USER_IDS:
            return False
        now = time.monotonic()
        last = self.last_cmd.get(user_id)
        if last is not None and (now - last) < cooldown:
            return True
        self.last_cmd[user_id] = now
        if len(self.last_cmd) > 20000:
            cutoff = now - 3600
            self.last_cmd = {k: v for k, v in self.last_cmd.items() if v > cutoff}
        return False

    def _notify_slow(self, chat_id: int, user_id: int):
        now = time.monotonic()
        if now - self._spam_notified.get(user_id, 0) > 15:
            self._spam_notified[user_id] = now
            try:
                self.bot.send_message(chat_id, "⏳ Не так быстро. Подождите пару секунд и повторите.")
            except Exception:
                pass

    @staticmethod
    def _is_valid_public_ipv4(text: str) -> bool:
        """Публичный IPv4: не loopback, не приватный, не link-local, не резервированный."""
        try:
            ip = ipaddress.ip_address((text or "").strip())
        except ValueError:
            return False
        if ip.version != 4:
            return False
        return (not ip.is_private and not ip.is_loopback
                and not ip.is_link_local and not ip.is_reserved
                and not ip.is_multicast and not ip.is_unspecified)

    @staticmethod
    def _is_valid_hostname(text: str) -> bool:
        """Допустимое доменное имя для SNI-донора (без пробелов и shell-метасимволов)."""
        h = (text or "").strip().lower()
        if not h or len(h) > 253:
            return False
        if h.endswith("."):
            h = h[:-1]
        if not 0 < h.count(".") < 4:
            return False
        return all(
            re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", part) and len(part) <= 63
            for part in h.split(".")
        )

    @staticmethod
    def _md_escape(text: str) -> str:
        """Экранирование спецсимволов Telegram Markdown в пользовательских данных."""
        return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))

    @staticmethod
    def _ssh_env_line(sni_hostname: str, sni_ip: Optional[str]) -> str:
        """Строка среды для setup_vps.sh: значения в кавычках через shlex,
        чтобы подставленный hostname/IP не могли выполнить команды на VPS."""
        return f"export SNI_HOSTNAME={shlex.quote(sni_hostname)} SNI_IP={shlex.quote(sni_ip or '')}\n"

    def _pending_over_limit(self, user_id: int) -> bool:
        """True, если у пользователя уже есть разрешённое число активных счетов."""
        if MAX_PENDING_PER_USER <= 0:
            return False
        count = sum(
            1 for _, row in self.payment_system.get_active_invoices().items()
            if row.get('user_id') == user_id
        )
        return count >= MAX_PENDING_PER_USER

    def _resume_pending(self, chat_id: int, user_id: int) -> bool:
        """При наличии активного счёта отправляет кнопку оплаты. True, если счёт найден."""
        for _, row in self.payment_system.get_active_invoices().items():
            if row.get('user_id') == user_id and row.get('pay_url'):
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("💳 Оплатить активный счёт", url=row['pay_url']))
                self.bot.send_message(
                    chat_id,
                    "⏳ У вас уже есть активный счёт:",
                    reply_markup=markup,
                )
                return True
        return False

    # ===== Поддержка: автоответ + пересылка владельцу =====
    SUPPORT_RULES = [
        (("оплат", "крипт", "usdt", "звезд", "stars", "счёт", "счет",
          "платеж", "платёж", "инвойс", "монет", "crypto"), "оплата"),
        (("установ", "скрипт", "setup", "ssh", "пароль", "root", "хван",
          "sanek", "sani", "xray"), "install"),
        (("ip", "адрес", "address"), "ip"),
        (("vps", "сервер", "площад", "adminvps", "купить", "коробочк",
          "аренд", "vds", "хостинг"), "vps"),
        (("роутер", "keenetic", "кеенетик", "рутер", "загруз",
          "файл", "конфиг", "архив", "zip", "прошивк"), "router"),
        (("не работа", "не грузит", "не открыва", "падает", "тормоз",
          "завис", "ошиб", "не получ", "не могу", "сломал", "белый экран"), "problem"),
        (("человек", "оператор", "живой", "специалист", "админ",
          "поддержк", "помощь", "help", "спасти"), "human"),
        (("привет", "здравств", "добрый", "хай", "hello", "ку"), "hello"),
        (("спасибо", "благодар", "спс", "сенкс"), "thanks"),
        (("статус", "где мой", "мой заказ", "myorders", "обнов"), "order"),
    ]

    SUPPORT_ANSWERS = {
        "оплата": (
            "💳 *Оплата простыми словами:*\n\n"
            "• Крипта (Crypto Bot): нажмите кнопку «Оплатить 1500 ₽» в боте — "
            "откроется платёж в USDT, оплачиваете картой/криптой.\n"
            "• Звёзды: кнопка «⭐ Оплатить звёздами» — берётся из баланса Telegram "
            "через приложение на телефоне.\n\n"
            "Оплата разовая, 1500 ₽ или 750 ⭐, за саму настройку. "
            "Сам сервер оплачивается отдельно у провайдера (от 150 ₽/мес)."
        ),
        "install": (
            "🔧 *Установка Xray — что делать:*\n\n"
            "Вариант А (ничего не делаете вы): после оплаты жмите "
            "«🔑 Авто-установка по SSH» и пришлите боту пароль от сервера. "
            "Бот сам всё поставит. Если нужно включить вход по паролю на сервере — "
            "бот пришлёт точные команды, просто скопируйте их.\n"
            "Вариант Б (сами): кнопка «🧰 Сам запущу скрипт» → скачиваете "
            "`setup_vps.sh`, запускаете на сервере и присылаете сюда блок вывода.\n\n"
            "Подробнее: /guide"
        ),
        "ip": (
            "🌐 *Где взять IP-адрес сервера:*\n"
            "После покупки сервера провайдер присылает письмо с адресом вида "
            "`123.45.67.89`. Его же видно в кабинете провайдера (раздел «Мои серверы»). "
            "Это 4 числа через точки. Пришлите его боту — и продолжайте /buy.\n\n"
            "Сервера ещё нет? Смотрите → /vps"
        ),
        "vps": (
            "📦 *Про сервер (VPS) простыми словами:*\n"
            "Это ваша маленькая «коробочка» за границей, через которую идёт интернет. "
            "Покупается у провайдера отдельно (от 150 ₽/мес), это не наша оплата.\n\n"
            "Завести за 1 минуту: /vps\n"
            "Почему это лучше покупного VPN: /faq"
        ),
        "router": (
            "🎛 *Ищем роутер?*\n"
            "Если ещё не купили — нажмите /router: я подберу модель под вашу "
            "ситуацию (бюджетная/средняя/мощная, а если уже есть Keenetic — проверим её).\n\n"
            "*Уже есть роутер:* назовите модель (например «Keenetic Giga» или "
            "«TP-Link Archer AX55») — подскажу, что делать.\n\n"
            "*Как загрузить настройки:* после установки бот пришлёт ZIP-архив — "
            "загружаете его в роутер и всё работает у всей семьи.\n\n"
            "Полный список устройств: /guide"
        ),
        "problem": (
            "🛠 *Не работает? Действуем по шагам:*\n"
            "1. Проверьте, что сервер оплачен (у провайдера) и запущен.\n"
            "2. Перезагрузите роутер / приложение.\n"
            "3. Не помогло? Обновим маскировку: /myorders → «Обновить существующий заказ».\n\n"
            "Если и после этого не работает — жмите «человек/специалист», "
            "передам ваш вопрос человеку."
        ),
        "human": (
            "👨‍💻 *Передаю ваш вопрос человеку.*\n"
            "Сейчас наберёт поддержка @beliy_obhodchik_support_bot. "
            "Опишите, пожалуйста, что случилось, и приложите скриншот, если есть."
        ),
        "hello": (
            "👋 Здравствуйте! Я помогу навести порядок с интернетом. "
            "Начнём? Нажмите /buy или задайте вопрос своими словами."
        ),
        "thanks": (
            "😊 Пожалуйста! Если что-то ещё понадобится — я здесь: /support"
        ),
        "order": (
            "🗂 *Ваши заказы:*\n"
            "Команда /myorders покажет список. Там же — кнопка "
            "«Обновить существующий заказ», если что-то перестало работать."
        ),
    }

    def _support_auto_answer(self, text: str):
        """Возвращает ответ на типовой вопрос или None, если вопрос не понят."""
        tl = (text or "").lower()

        # Сначала ищем конкретные модели роутеров (специфичные ответы)
        t = tl
        if any(name in t for name in ("giga", "ultra", "hopper", "omni", "keenetic omni", "keenetic giga")):
            return (
                "🎛 *Keenetic — отличный выбор!*\n"
                "Ваша модель Keenetic полностью поддерживается: я подготовлю готовый "
                "архив для загрузки в роутер «в один клик».\n\n"
                "Порядок: 1) заведите сервер → /vps \n"
                "2) жмите /buy и пришлите боту пароль от сервера — я сам всё настрою.\n"
                "После установки пришлю готовый ZIP для вашего Keenetic."
            )
        if "archer" in t or "tp-link" in t:
            return (
                "🌐 *TP-Link Archer — рабочий вариант.*\n"
                "У таких роутеров есть встроенный VPN-клиент, но конфиг подгружается "
                "не «в один клик», как на Keenetic. Я подготовлю все данные (VLESS URL), "
                "а вы импортируете их в приложение на телефоне или в OpenWrt на роутере.\n\n"
                "Закажите настройку /buy — в комплекте будет инструкция именно под TP-Link."
            )
        if "redmi" in t or "xiaomi" in t:
            return (
                "📶 *Xiaomi/Redmi — вариант для тех, кто не боится настройки.*\n"
                "Из коробки VPN-клиента нет — понадобится установить OpenWrt или "
                "подключить телефон через приложение. Бот выдаст все ключи и короткую "
                "инструкцию. Сложнее, чем Keenetic, но работает.\n\n"
                "Рекомендация: для «один клик и забыл» лучше взять Keenetic. Подобрать: /router"
            )

        for keywords, topic in self.SUPPORT_RULES:
            if any(kw in tl for kw in keywords):
                return self.SUPPORT_ANSWERS[topic]
        return None

    def _main_menu_inline_keyboard(self) -> 'types.InlineKeyboardMarkup':
        """Главное меню: русские кнопки под текстом (вместо нижней клавиатуры и команд).
        Кнопки шлют callback cmd_*, диспетчер вызывает те же функции, что и команды."""
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🛒 Купить настройку", callback_data="cmd_buy"),
            types.InlineKeyboardButton("🎛 Подобрать роутер", callback_data="cmd_router"),
        )
        kb.add(
            types.InlineKeyboardButton("🎞 Обход DPI", callback_data="cmd_dpi"),
            types.InlineKeyboardButton("🖥 Как завести сервер", callback_data="cmd_vps"),
        )
        kb.add(
            types.InlineKeyboardButton("📖 Полная инструкция", callback_data="cmd_guide"),
            types.InlineKeyboardButton("❓ Частые вопросы", callback_data="cmd_faq"),
        )
        kb.add(
            types.InlineKeyboardButton("📦 Мои заказы", callback_data="cmd_myorders"),
            types.InlineKeyboardButton("🎧 Чат поддержки", callback_data="cmd_support"),
        )
        return kb

    def _show_welcome(self, chat_id: int):
        """Приветственное сообщение с главным меню (inline-кнопки на русском).
        Используется из /start и после отмены заказа / выхода из режима поддержки."""
        welcome_text = (
            "👋 *Здравствуйте! Я — БелыйОбходчик.*\n\n"
            "Понимаю вашу боль без технических слов:\n\n"
            "❌ *Сейчас:* Ютуб не грузится, видео «крутится» часами, сайты не открываются, "
            "приложения падают.\n\n"
            "✅ *Вы хотите:* чтобы всё работало как раньше — и вы не думали, *как* это устроено.\n\n"
            "➡️ *Что нужно от вас (один раз):*\n"
            "1. Купить крошечный «сервер-коробочку» за границей — от 150 ₽/мес "
            "(для сравнения: одна поездка на маршрутке). По шагам поможем — кнопка ниже.\n"
            "2. Оплатить настройку: 1500 ₽ или 750 ⭐\n\n"
            "➡️ *Что мы сделаем (дальше всё само):*\n"
            "• Подключимся к вашему серверу и настроим его автоматически\n"
            "• Соберём готовые настройки прямо для вашего роутера (Keenetic, и др.)\n"
            "• При блокировках обновим донора сами\n"
            "• Поддержка 30 дней\n\n"
            "🎁 *Почему это лучше, чем «купить VPN за 200₽»:* наш сервер принадлежит *вам* — "
            "высокая скорость, личный не забитый IP, без падений и слежки.\n\n"
            "⬇️ *Выберите действие ниже:*"
        )
        self.bot.send_message(
            chat_id,
            welcome_text,
            parse_mode='Markdown',
            reply_markup=self._main_menu_inline_keyboard(),
        )

    def _deliver_dpi_setup(self, chat_id: int, user_id: int, stars: int = 0,
                           payment_id: Optional[str] = None) -> bool:
        """Услуга «Ютуб/Дискорд/Инстаграм/ТикТок Твиттер БЕЗ сервера» (Zapret/NFQWS).
        После оплаты просто присылаем установочный скрипт + понятную инструкцию.
        Никакой VPS, IP и SSH не нужно — роутер обходит замедления сам.
        Идемпотентно: для оплаченного payment_id скрипт отправляется один раз."""
        if payment_id and self.payment_system.find_paid_order(payment_id):
            self.bot.send_message(
                chat_id,
                "✅ Настройка по этому платежу уже была отправлена. Если файл потерялся — "
                "напишите в /support, пришлём повторно.",
                reply_markup=self._main_menu_inline_keyboard()
            )
            return True
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_nfqws.sh")
        if not os.path.exists(script_path):
            self.bot.send_message(
                chat_id,
                "❌ Скрипт setup_nfqws.sh не найден на сервере бота. Напишите поддержку: @beliy_obhodchik_support_bot",
                reply_markup=self._main_menu_inline_keyboard()
            )
            return False
        try:
            with open(script_path, 'rb') as f:
                self.bot.send_document(
                    chat_id, f,
                    caption=(
                        "🎬 *Обход Ютуба, Дискорда, Инстаграма, ТикТока, Твиттера — БЕЗ сервера* "
                        "(Zapret / NFQWS)\n\n"
                        "Загрузите `setup_nfqws.sh` на роутер и запустите:\n"
                        "`sudo bash setup_nfqws.sh`\n\n"
                        "🐧 *Где запускать:* Keenetic (Entware), OpenWrt, любой роутер с opkg/apk.\n"
                        "🪟 *Есть только ПК?* Поставьте ByeDPI/GoodbyeDPI на Windows/macOS — "
                        "эффект тот же, инструкцию пришлём после оплаты.\n\n"
                        "ℹ️ *Честно:* скрипт обходит замедление/блокировку по SNI-признаку "
                        "(обычный случай у провайдеров РФ). Если у вас блокировка по IP-подсети "
                        "целиком — нужен свой сервер: /buy"
                    ),
                    parse_mode='Markdown'
                )
            self.bot.send_message(
                chat_id,
                "📖 *Дальше — по шагам (10 минут):*\n\n"
                "1️⃣ Скачайте файл `setup_nfqws.sh` выше.\n"
                "2️⃣ Загрузите его на роутер (через панель Keenetic/SSH или USB).\n"
                "3️⃣ Выполните `sudo bash setup_nfqws.sh`.\n"
                "4️⃣ Скрипт сам скачает Zapret, настроит стратегию под YouTube+Discord+"
                "Instagram+TikTok и включит автозапуск.\n"
                "5️⃣ Откройте Ютуб — должно работать сразу после перезагрузки страницы.\n\n"
                "❓ Если что-то пошло не так — спросите в /support: я сам отвечу, "
                "а сложный случай передам человеку.",
                reply_markup=self._main_menu_inline_keyboard()
            )
            # Заказ в БД (идемпотентно): DPI — это товар без сервера и ключей.
            if payment_id:
                order_id = self.payment_system.create_order(
                    payment_id=payment_id,
                    user_id=user_id,
                    config_path="setup_nfqws.sh",
                    server_ip=None,
                    uuid=None,
                    sni_hostname=None,
                )
                self.payment_system.mark_order_delivered(order_id)
            return True
        except Exception as e:
            logger.error("Ошибка доставки настройки NFQWS: %s", e)
            self.bot.send_message(chat_id, "❌ Не удалось отправить файл. Напишите поддержку: @beliy_obhodchik_support_bot", reply_markup=self._main_menu_inline_keyboard())
            return False

    def _forward_to_owner(self, message) -> bool:
        """Пересылает вопрос владельцам. Возвращает True, если уведомили хоть одного."""
        if not ADMIN_USER_IDS:
            return False
        forwarded = False
        for admin_id in ADMIN_USER_IDS:
            try:
                fwd = self.bot.forward_message(
                    admin_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                self.support_forwards[fwd.message_id] = (message.from_user.id, message.chat.id)
                forwarded = True
                # Не даём карте сообщений расти бесконечно
                if len(self.support_forwards) > 1000:
                    for k in list(self.support_forwards)[:500]:
                        self.support_forwards.pop(k, None)
            except Exception as e:
                logger.error("Не удалось переслать вопрос владельцу %s: %s", admin_id, e)
        return forwarded

    def _reply_from_owner(self, message) -> bool:
        """Если владелец ответил на пересланное сообщение — доставляем текст пользователю."""
        reply = getattr(message, "reply_to_message", None)
        if not reply or message.from_user.id not in ADMIN_USER_IDS:
            return False
        ticket = self.support_forwards.get(reply.message_id)
        if not ticket:
            return False
        user_id, chat_id = ticket
        try:
            self.bot.send_message(chat_id, f"📩 *Ответ поддержки:*\n{message.text}")
            self.bot.send_message(message.chat.id, f"✉️ Ответ отправлен пользователю {user_id}.")
            # Тикет отработан — убираем, чтобы карта не разрасталась
            with self._lock:
                self.support_forwards.pop(reply.message_id, None)
            return True
        except Exception as e:
            logger.error("Не удалось доставить ответ пользователю %s: %s", user_id, e)
            self.bot.send_message(message.chat.id, f"❌ Не удалось доставить ответ (id {user_id}).")
            return True

    def register_handlers(self):
        """Регистрация обработчиков команд"""
        
        # Системное меню команд (/buy, /dpi…) намеренно не регистрируем:
        # вместо команд и их описаний используем inline-кнопки на русском
        # (см. _main_menu_inline_keyboard в приветствии).
        
        @self.bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            self._show_welcome(message.chat.id)
        
        @self.bot.message_handler(commands=['dpi'])
        def make_dpi_order(message):
            """Услуга «Без сервера»: обход DPI на роутере/ПК — без покупки VPS."""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            payment_id = "dpi_" + hashlib.md5(
                f"{user_id}{time.time()}{secrets.token_hex(4)}".encode()
            ).hexdigest()[:12]
            username = getattr(message.from_user, 'username', None) or str(user_id)
            self.payment_system.create_payment(user_id, username, amount=DPI_RUB_PRICE, payment_id=payment_id)
            self.user_data[user_id] = {"payment_id": payment_id}
            self.user_states[user_id] = None

            _text = (
                "🎞 *Обход DPI без сервера*\n\n"
                "Вернём ютуб, дискорд, инстаграм, тикток и твиттер "
                "на роутере/ПК/телефоне — **без покупки VPS-сервера**.\n\n"
                "Что вы получите:\n"
                "• Скрипт `setup_nfqws.sh` — установит Entware и nfqws2 "
                "(методика Zapret), про обход DPI-замедления\n"
                "• Включим автозапуск на роутере\n"
                "• Поддержка: поможем подобрать стратегию\n\n"
                f"💰 Цена: **{DPI_RUB_PRICE} ₽** (крипто) или **{DPI_STARS_PRICE} ⭐** (звёзды)\n\n"
                "⚠️ Честно: это обход замедления/блокировок по DPI. Если провайдер "
                "режет по IP-подсетям целиком — нужен свой сервер: /vps"
            )
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(
                f"💳 Оплатить {DPI_RUB_PRICE} ₽ (Crypto Bot)", callback_data="make_dpi_payment"
            ))
            markup.add(types.InlineKeyboardButton(
                f"⭐ Оплатить {DPI_STARS_PRICE} звёздами", callback_data="pay_dpi_stars"
            ))
            markup.add(types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_purchase"))
            self.bot.send_message(
                message.chat.id, _text, parse_mode="Markdown", reply_markup=markup
            )
        @self.bot.message_handler(commands=['buy'])
        def start_purchase(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            username = getattr(message.from_user, 'username', None) or str(user_id)
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            
            # Проверяем активные заказы
            orders = self.payment_system.get_user_orders(user_id)
            active_orders = [o for o in orders if o['payment_status'] == 'paid']
            
            if active_orders:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔄 Обновить существующий заказ", callback_data="update_existing"))
                markup.add(types.InlineKeyboardButton("➕ Создать новый заказ", callback_data="create_new"))
                
                self.bot.send_message(
                    message.chat.id,
                    "У вас уже есть активные заказы. Что хотите сделать?",
                    reply_markup=markup
                )
                return

            # Антиспам: не создаём новые счета, пока не оплачен активный
            if self._pending_over_limit(user_id):
                if not self._resume_pending(message.chat.id, user_id):
                    self.bot.send_message(
                        message.chat.id,
                        "⏳ У вас уже есть активный счёт. Оплатите его и повторите попытку.",
                        reply_markup=self._main_menu_inline_keyboard(),
                    )
                return

            # Начинаем процесс покупки
            self.user_states[user_id] = "awaiting_server_ip"
            self.user_data[user_id] = {}
            
            # Создаём платеж
            payment_id = self.payment_system.create_payment(user_id, username, amount=VPS_RUB_PRICE)
            self.user_data[user_id]['payment_id'] = payment_id
            
            instruction = f"""
💰 *Стоимость настройки: {VPS_RUB_PRICE} ₽ один раз* или {STARS_PRICE} ⭐

Что вы получаете:
• Готовые настройки для вашего роутера (просто загрузите файл)
• Подключение вашего сервера — делаем сами, вы ничего не настраиваете
• Автообновление при блокировках — мы следим сами
• Поддержка 30 дней

📌 *Зачем нужен «свой сервер»?*
Это небольшая удалённая коробочка за границей, через которую идёт ваш интернет.
Оплачивается отдельно у провайдера (от 150 ₽/мес), к нам не относится.
Именно он делает вас «своим человеком» в другом мире — без общих каналов и падений.
Пошагово как завести за 1 минуту: /vps
И почему это лучше покупного VPN: /faq

Для продолжения:
1. Введите IP адрес вашего сервера
   (например: 123.45.67.89)

Если сервера ещё нет — сначала нажмите /vps и следуйте шагам, потом вернитесь сюда.

Введите IP вашего сервера:
            """
            
            self.bot.send_message(message.chat.id, instruction, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda message: self.get_user_state(message.from_user.id) == "awaiting_server_ip")
        def process_server_ip(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            server_ip = message.text.strip()
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            
            # Валидация публичного IPv4 (не private/loopback/link-local/мультикаст/999.999.999.999)
            if not self._is_valid_public_ipv4(server_ip):
                self.bot.send_message(
                    message.chat.id,
                    "❌ Похоже, это не публичный IP сервера. Введите публичный IPv4 "
                    "(например: 123.45.67.89) — адрес из письма провайдера:"
                )
                return
            
            self.user_data[user_id]['server_ip'] = server_ip
            self.user_states[user_id] = "awaiting_payment"
            
            # Получаем лучший SNI-донор
            donor = self.sni_db.get_best_donor()
            
            if not donor:
                self.bot.send_message(
                    message.chat.id,
                    "❌ Временно нет доступных SNI-доноров. Попробуйте позже.",
                    reply_markup=self._main_menu_inline_keyboard(),
                )
                self.user_states[user_id] = None
                return
            
            self.user_data[user_id]['sni_hostname'] = donor['hostname']
            self.user_data[user_id]['sni_ip'] = donor['ip_address']
            
            # Показываем информацию о доноре
            donor_info = f"""
✅ Найден рабочий SNI-донор:
• Маскировка: `{self._md_escape(donor['hostname'])}`
• Страна: {donor['country_code']}
• Успешность: {donor['success_rate']:.0%}

Теперь можно оплатить настройку.
            """
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("💳 Оплатить 1500 ₽ (Crypto Bot)", callback_data="make_payment"))
            markup.add(types.InlineKeyboardButton(f"⭐ Оплатить {STARS_PRICE} звёздами", callback_data="pay_stars"))
            markup.add(types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_purchase"))
            
            self.bot.send_message(
                message.chat.id, 
                donor_info, 
                parse_mode='Markdown',
                reply_markup=markup
            )
        
        @self.bot.message_handler(func=lambda m: self.get_user_state(m.from_user.id) == "awaiting_self_output")
        def handle_self_output(message):
            """Путь A: пользователь сам запустил setup_vps.sh и прислал блок параметров."""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            parsed = self._parse_vps_output(message.text or "")
            if not parsed:
                self.bot.send_message(
                    message.chat.id,
                    "❌ Не вижу блок `===BELIY-OBHODCHIK-VLESS===` ... `===END===` в сообщении.\n"
                    "Запустите setup_vps.sh на VPS и пришлите сюда весь вывод целиком.",
                    reply_markup=self._main_menu_inline_keyboard(),
                )
                return
            self._save_setup_and_deliver(user_id, message.chat.id, parsed,
                                         self.user_data.get(user_id, {}).get('sni_hostname'),
                                         self.user_data.get(user_id, {}).get('sni_ip'),
                                         install_method="self")
        
        @self.bot.message_handler(func=lambda m: self.get_user_state(m.from_user.id) == "awaiting_ssh_password")
        def handle_ssh_password(message):
            """Путь B: авто-установка по SSH — приняли пароль root, запускаем установку."""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            password = message.text.strip()
            if not password or '\n' in password or password.startswith('/'):
                self.bot.send_message(message.chat.id, "❌ Пришлите пароль root одной строкой.")
                return
            self.bot.send_message(
                message.chat.id,
                "⏳ Подключаюсь к вашему VPS и ставлю Xray... Это занимает 1-3 минуты."
            )
            ud = self.user_data.setdefault(user_id, {})
            with self._lock:
                ud['ssh_password'] = password
            threading.Thread(
                target=self._run_ssh_install,
                args=(user_id, message.chat.id, password, ud),
                daemon=True,
            ).start()
        
        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callback(call):
            if not self._guard_callback(call):
                return
            user_id = call.from_user.id

            # Кнопки главного меню: cmd_* вызывают те же обработчики, что и команды.
            # Антифлуд выполнит сам обработчик-функция — здесь пропускаем, чтобы не задвоить.
            if (call.data or "").startswith("cmd_"):
                action = call.data[4:]
                handlers = {
                    "buy": start_purchase,
                    "dpi": make_dpi_order,
                    "router": send_router_help,
                    "vps": send_vps_help,
                    "guide": send_guide,
                    "faq": send_faq,
                    "support": send_support,
                    "myorders": show_orders,
                }
                if action == "menu":
                    try:
                        self.bot.answer_callback_query(call.id)
                    except Exception:
                        pass
                    self._show_welcome(call.message.chat.id)
                    return
                handler = handlers.get(action)
                if handler:
                    try:
                        self.bot.answer_callback_query(call.id)
                    except Exception:
                        pass
                    fake = _CallbackMessage(call)
                    handler(fake)
                return

            if self._antiflood(user_id, CALLBACK_COOLDOWN):
                self._notify_slow(call.message.chat.id, user_id)
                return
            
            if call.data == "make_dpi_payment":
                payment_id = self.user_data.get(user_id, {}).get("payment_id")
                if not payment_id:
                    self._show_welcome(call.message.chat.id)
                    return
                if not self.crypto_pay:
                    self.bot.send_message(
                        call.message.chat.id,
                        "⚠️ Оплата временно недоступна. Обратитесь в поддержку: @beliy_obhodchik_support_bot",
                        reply_markup=self._main_menu_inline_keyboard(),
                    )
                    return
                if self._pending_over_limit(user_id):
                    if not self._resume_pending(call.message.chat.id, user_id):
                        self.bot.send_message(
                            call.message.chat.id,
                            "⏳ У вас уже есть активный счёт. Оплатите его и повторите попытку.",
                            reply_markup=self._main_menu_inline_keyboard(),
                        )
                    return
                try:
                    invoice = self.crypto_pay.create_invoice(
                        amount_rub=DPI_RUB_PRICE,
                        payload=payment_id,
                    )
                    invoice_id = invoice["invoice_id"]
                    pay_url = invoice["pay_url"]
                    with self._lock:
                        self.pending_payments[payment_id] = {
                            "payment_id": payment_id,
                            "user_id": user_id,
                            "chat_id": call.message.chat.id,
                            "pay_url": pay_url,
                        }
                        self.payment_system.create_invoice_record(
                            invoice_id=invoice_id,
                            payment_id=payment_id,
                            user_id=user_id,
                            chat_id=call.message.chat.id,
                            server_ip=None,
                            sni_hostname=None,
                            pay_url=pay_url,
                        )
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("💳 Оплатить в Crypto Bot", url=pay_url))
                    self.bot.send_message(
                        call.message.chat.id,
                        "💳 *Счёт создан на %d ₽* (оплата в USDT через Crypto Bot)\n\n"
                        "Скрипт `setup_nfqws.sh` придёт автоматически сразу после оплаты." % DPI_RUB_PRICE,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                except Exception as e:
                    logger.error("Ошибка создания DPI-счёта: %s", e)
                    self.bot.send_message(
                        call.message.chat.id,
                        "❌ Не удалось создать счёт. Попробуйте позже или напишите в поддержку: @beliy_obhodchik_support_bot\n(" + str(e)[:120] + ")",
                        reply_markup=self._main_menu_inline_keyboard(),
                    )

            elif call.data == "pay_dpi_stars":
                payment_id = self.user_data.get(user_id, {}).get("payment_id")
                if not payment_id:
                    self._show_welcome(call.message.chat.id)
                    return
                if self._pending_over_limit(user_id):
                    if not self._resume_pending(call.message.chat.id, user_id):
                        self.bot.send_message(
                            call.message.chat.id,
                            "⏳ У вас уже есть активный счёт. Оплатите его и повторите попытку.",
                            reply_markup=self._main_menu_inline_keyboard(),
                        )
                    return
                prices = [types.LabeledPrice(
                    label="БелыйОбходчик — обход DPI без сервера",
                    amount=DPI_STARS_PRICE,
                )]
                # Запоминаем звёздный счёт в БД: после перезапуска бот сможет его найти
                with self._lock:
                    self.payment_system.create_invoice_record(
                        invoice_id=f"stars_dpi_{payment_id}",
                        payment_id=payment_id,
                        user_id=user_id,
                        chat_id=call.message.chat.id,
                        server_ip=None,
                        sni_hostname=None,
                        pay_url="",
                        source="stars",
                    )
                self.bot.send_invoice(
                    call.message.chat.id,
                    "БелыйОбходчик — обход DPI без сервера",
                    "Скрипт установки Entware + nfqws2 на роутер для обхода DPI (YouTube, Discord, Instagram, TikTok).",
                    payment_id,
                    "",
                    "XTR",
                    prices,
                )

            if call.data == "make_payment":
                # Создаём счёт в Crypto Bot
                payment_id = self.user_data.get(user_id, {}).get('payment_id')
                if not payment_id:
                    self._show_welcome(call.message.chat.id)
                    return

                if not self.crypto_pay:
                    self.bot.send_message(
                        call.message.chat.id,
                        "⚠️ Оплата временно недоступна. Обратитесь в поддержку: @beliy_obhodchik_support_bot",
                        reply_markup=self._main_menu_inline_keyboard(),
                    )
                    return

                # Антиспам: максимум активных счетов на пользователя
                if self._pending_over_limit(user_id):
                    if not self._resume_pending(call.message.chat.id, user_id):
                        self.bot.send_message(
                            call.message.chat.id,
                            "⏳ У вас уже есть активный счёт. Оплатите его и повторите попытку.",
                            reply_markup=self._main_menu_inline_keyboard(),
                        )
                    return

                try:
                    with self._lock:
                        ud = self.user_data.get(user_id, {})
                        invoice = self.crypto_pay.create_invoice(
                            amount_rub=VPS_RUB_PRICE,
                            payload=payment_id,
                        )
                        invoice_id = invoice['invoice_id']
                        pay_url = invoice['pay_url']

                        # Запоминаем ожидание оплаты
                        self.pending_payments[payment_id] = {
                            'payment_id': payment_id,
                            'user_id': user_id,
                            'chat_id': call.message.chat.id,
                            'server_ip': ud.get('server_ip'),
                            'sni_hostname': ud.get('sni_hostname'),
                            'pay_url': pay_url,
                        }
                        self.payment_system.create_invoice_record(
                            invoice_id=invoice_id,
                            payment_id=payment_id,
                            user_id=user_id,
                            chat_id=call.message.chat.id,
                            server_ip=ud.get('server_ip'),
                            sni_hostname=ud.get('sni_hostname'),
                            pay_url=pay_url,
                        )

                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("💳 Оплатить в CryptoBot", url=pay_url))

                    self.bot.send_message(
                        call.message.chat.id,
                        "💳 *Счёт создан на %d ₽* (оплата в USDT через Crypto Bot)\n\n"
                        "Нажмите кнопку ниже и оплатите. Конфиг придёт автоматически сразу после оплаты." % VPS_RUB_PRICE,
                        parse_mode='Markdown',
                        reply_markup=markup,
                    )
                except Exception as e:
                    logger.error("Ошибка создания счёта: %s", e)
                    self.bot.send_message(
                        call.message.chat.id,
                        f"❌ Не удалось создать счёт. Попробуйте позже или напишите в поддержку: @beliy_obhodchik_support_bot\n({str(e)[:120]})",
                        reply_markup=self._main_menu_inline_keyboard(),
                    )
                
            elif call.data == "pay_stars":
                payment_id = self.user_data.get(user_id, {}).get('payment_id')
                if not payment_id:
                    self._show_welcome(call.message.chat.id)
                    return
                if self._pending_over_limit(user_id):
                    if not self._resume_pending(call.message.chat.id, user_id):
                        self.bot.send_message(
                            call.message.chat.id,
                            "⏳ У вас уже есть активный счёт. Оплатите его и повторите попытку.",
                            reply_markup=self._main_menu_inline_keyboard(),
                        )
                    return
                with self._lock:
                    ud = self.user_data.get(user_id, {})
                    # Запоминаем звёздный счёт в БД вместе с IP сервера и SNI-донором:
                    # после перезапуска бот найдёт заказ по payment_id и доведёт доставку.
                    self.payment_system.create_invoice_record(
                        invoice_id=f"stars_{payment_id}",
                        payment_id=payment_id,
                        user_id=user_id,
                        chat_id=call.message.chat.id,
                        server_ip=ud.get('server_ip'),
                        sni_hostname=ud.get('sni_hostname'),
                        pay_url="",
                        source="stars",
                    )
                prices = [types.LabeledPrice(
                    label="БелыйОбходчик — автоматическая настройка",
                    amount=STARS_PRICE,
                )]
                self.bot.send_invoice(
                    call.message.chat.id,
                    "БелыйОбходчик — автоматическая настройка",
                    "Настройка VLESS Reality на вашем VPS + конфиг для Keenetic + инструкция.",
                    payment_id,
                    "",
                    "XTR",
                    prices,
                )
                
            elif call.data == "install_self":
                ud = self.user_data.get(user_id, {})
                sni_hostname = ud.get('sni_hostname')
                sni_ip = ud.get('sni_ip')
                if not sni_hostname:
                    self._show_welcome(call.message.chat.id)
                    return
                self.user_states[user_id] = "awaiting_self_output"
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_vps.sh")
                if not os.path.exists(script_path):
                    self.bot.send_message(call.message.chat.id, "❌ Скрипт setup_vps.sh не найден на сервере бота.", reply_markup=self._main_menu_inline_keyboard())
                    return
                with open(script_path, 'rb') as f:
                    self.bot.send_document(
                        call.message.chat.id, f,
                        caption=(
                            f"🔧 *Способ 1: сами запустите скрипт*\n\n"
                            f"1. Скачайте и загрузите `setup_vps.sh` на свой VPS\n"
                            f"2. Выполните:\n"
                            f"`chmod +x setup_vps.sh`\n"
                            f"`sudo SNI_HOSTNAME='{sni_hostname}' bash setup_vps.sh`\n"
                            + (f"Или с указанием IP донора:\n`sudo SNI_HOSTNAME='{sni_hostname}' SNI_IP='{sni_ip}' bash setup_vps.sh`\n\n" if sni_ip else "")
                            + "3. Пришлите сюда ВЕСЬ вывод блоком `===BELIY-OBHODCHIK-VLESS===` ... `===END===`\n"
                        )
                    )
                self.bot.send_message(
                    call.message.chat.id,
                    "Жду вывод скрипта. Просто пришлите его целиком одним сообщением."
                )
            
            elif call.data == "install_ssh":
                ud = self.user_data.get(user_id, {})
                if not ud.get('server_ip'):
                    self._show_welcome(call.message.chat.id)
                    return
                if paramiko is None:
                    self.bot.send_message(call.message.chat.id, "❌ SSH-модуль не установлен. Пока выберите «сам запущу скрипт».", reply_markup=self._main_menu_inline_keyboard())
                    return
                self.user_states[user_id] = "awaiting_ssh_password"
                self.bot.send_message(
                    call.message.chat.id,
                    "🔑 Пришлите пароль `root` от вашего VPS одной строкой. "
                    "Бот сам подключится по SSH, поставит Xray и заберёт ключи."
                )

            elif call.data == "retry_ssh_install":
                ud = self.user_data.get(user_id, {})
                password = ud.get('ssh_password')
                if not ud.get('server_ip') or not ud.get('sni_hostname') or not password:
                    self._show_welcome(call.message.chat.id)
                    return
                self.user_states[user_id] = None
                self.bot.send_message(
                    call.message.chat.id,
                    "⏳ Повторяю попытку подключения по SSH и установки Xray... Это займёт 1-3 минуты."
                )
                threading.Thread(
                    target=self._run_ssh_install,
                    args=(user_id, call.message.chat.id, password, ud),
                    daemon=True,
                ).start()
            
            elif call.data == "cancel_purchase":
                self.user_states[user_id] = None
                self.user_data[user_id] = {}
                self._show_welcome(call.message.chat.id)
                
            elif call.data == "update_existing":
                orders = self.payment_system.get_user_orders(user_id)
                active_orders = [o for o in orders if o['payment_status'] == 'paid']
                
                if active_orders:
                    markup = types.InlineKeyboardMarkup()
                    for order in active_orders[:3]:  # Показываем первые 3
                        btn_text = f"📦 {order['order_id'][:8]}... ({order['server_ip']})"
                        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"update_{order['order_id']}"))
                    
                    self.bot.send_message(
                        call.message.chat.id,
                        "Выберите заказ для обновления:",
                        reply_markup=markup
                    )
                else:
                    self._show_welcome(call.message.chat.id)
            
            elif call.data.startswith("update_"):
                order_id = call.data.replace("update_", "")
                order = self.payment_system.get_order(order_id)
                if not order or order.get('user_id') != user_id:
                    # Чужой/несуществующий заказ — IDOR-защита: не даём тронуть чужие конфиги
                    self._show_welcome(call.message.chat.id)
                    return
                setup = self.payment_system.get_vps_setup(user_id)
                if not setup:
                    # Установка ещё не выполнена — отправляем прямиком к выбору способа
                    self.user_data.setdefault(user_id, {})
                    self.user_data[user_id].setdefault('server_ip', order.get('server_ip'))
                    self.user_data[user_id].setdefault('sni_hostname', order.get('sni_hostname'))
                    self.user_data[user_id]['payment_id'] = order.get('payment_id')
                    self._offer_install_choice(user_id, call.message.chat.id)
                    return

                donor = self.sni_db.get_best_donor()
                if not donor:
                    self.bot.send_message(call.message.chat.id, "❌ Не удалось найти новый SNI-донор. Попробуйте позже.", reply_markup=self._main_menu_inline_keyboard())
                    return

                # Смена донора в установке (ключи VPS не меняются), затем реальная доставка
                self.payment_system.update_vps_setup_sni(user_id, donor['hostname'], donor['ip_address'])
                self.user_data[user_id] = {
                    'payment_id': order.get('payment_id'),
                    'user_id': user_id,
                    'chat_id': call.message.chat.id,
                    'server_ip': setup['server_ip'],
                    'sni_hostname': donor['hostname'],
                    'sni_ip': donor['ip_address'],
                }
                self.bot.send_message(
                    call.message.chat.id,
                    f"🔄 Обновляю конфиг на новый SNI-донор: `{self._md_escape(donor['hostname'])}` "
                    f"(успешность {donor['success_rate']:.0%})...",
                    parse_mode='Markdown',
                )
                ok = self.generate_and_send_config(
                    user_id, call.message.chat.id, data=self.user_data[user_id]
                )
                if ok:
                    self.bot.send_message(
                        call.message.chat.id,
                        f"✅ *Конфиг обновлён и отправлен!*\n\n"
                        f"• Новый донор: `{self._md_escape(donor['hostname'])}`\n"
                        f"• Страна: {donor['country_code']}\n"
f"• Успешность: {donor['success_rate']:.0%}\n\n"
                    f"Загрузите новый архив в роутер Keenetic (вместо старого).",
                        parse_mode='Markdown',
                        reply_markup=self._main_menu_inline_keyboard(),
                    )
                else:
                    self.bot.send_message(
                        call.message.chat.id,
                        "❌ Не удалось сформировать конфиг. Напишите в поддержку: @beliy_obhodchik_support_bot",
                        reply_markup=self._main_menu_inline_keyboard(),
                    )
            
            elif call.data.startswith("router_"):
                key = call.data[len("router_"):]
                info = ROUTER_CATEGORIES.get(key)
                if not info:
                    self._show_welcome(call.message.chat.id)
                    return
                title, desc, models = info
                lines = [f"📡 *{title}*", "", desc, ""]
                for name, price, feats in models:
                    lines.append(f"• *{name}* — {price}")
                    lines.append(f"   {feats}")
                lines.append("")
                lines.append("🛠 *Дальше просто:*")
                lines.append("1. Купите/возьмите роутер из списка")
                lines.append("2. Заведите сервер за минуту: /vps")
                lines.append("3. Жмите /buy — я всё настрою за вас")
                lines.append("")
                if key == "keenetic_family":
                    lines.append("💡 Назовите модель (например «Giga» или «Ультра») — я проверю, подходит ли она.")
                else:
                    lines.append("💡 Если не уверены — напишите в /support, подскажем.")
                self.bot.send_message(call.message.chat.id, "\n".join(lines), parse_mode='Markdown', reply_markup=self._main_menu_inline_keyboard())

            elif call.data == "create_new":
                self.user_states[user_id] = None
                self.user_data[user_id] = {}
                self._show_welcome(call.message.chat.id)
        
        @self.bot.message_handler(commands=['myorders'])
        def show_orders(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            orders = self.payment_system.get_user_orders(user_id)
            
            if not orders:
                self.bot.send_message(message.chat.id, "📭 У вас пока нет заказов.", reply_markup=self._main_menu_inline_keyboard())
                return
            
            response = "📋 *Ваши заказы:*\n\n"
            
            for order in orders:
                status_emoji = "✅" if order['payment_status'] == 'paid' else "⏳"
                delivered_emoji = "📨" if order['delivered_at'] else "📭"
                server_ip = order['server_ip'] or '— (без сервера)'
                sni = order['sni_hostname'] or '—'
                
                response += f"""
{status_emoji} *Заказ {order['order_id'][:8]}...*
• Сервер: {server_ip}
• SNI: `{sni}`
• Статус оплаты: {order['payment_status']}
• Создан: {order['created_at'][:10]}
• Доставлен: {delivered_emoji}
                """
            
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown', reply_markup=self._main_menu_inline_keyboard())
        
        @self.bot.message_handler(commands=['vps'])
        def send_vps_help(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            vps_text = """
🇷🇺🛫 *Что такое «сервер» и как его завести за 1 минуту*

Простыми словами: это небольшая удалённая коробочка за границей,
через которую пойдёт ваш интернет. Покупается у провайдера отдельно
(к нашей плате за настройку отношения не имеет).

*Выбирайте сервер поближе:* {vps_recs}

🧭 *Мини-инструкция (4 шага, ~1 минута):*
1. Откройте ссылку провайдера выше и нажмите «Заказать сервер»
2. Выберите *страну поближе к вам* (например, Россия или Нидерланды).
   Параметры не важны — маленькие и дешёвые отлично подходят.
3. Придумайте *пароль* (любой, отправьте этот же пароль боту при установке).
   Оплатите картой или криптой — от 150 ₽/мес.
4. Дождитесь письма с IP-адресом и *пришлите этот IP сюда* (4 числа через точки), например `123.45.67.89` — и жмите /buy.

Подробная картинками-инструкция: /guide
            """.format(vps_recs=self._vps_recommendations_text())
            self.bot.send_message(message.chat.id, vps_text, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=self._main_menu_inline_keyboard())
        
        @self.bot.message_handler(commands=['router'])
        def send_router_help(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            markup = types.InlineKeyboardMarkup()
            for key, (title, _, _) in ROUTER_CATEGORIES.items():
                markup.add(types.InlineKeyboardButton(title, callback_data=f"router_{key}"))
            markup.add(types.InlineKeyboardButton("🏠 В главное меню", callback_data="cmd_menu"))
            self.bot.send_message(
                message.chat.id,
                "📡 *Подбор роутера*\n\n"
                "Нажмите, что подходит под вашу ситуацию, и я порекомендую модель "
                "с которой всё будет работать из коробки:\n\n"
                "*Важно:* для автоматической настройки проще всего роутер Keenetic "
                "— на нём всё ставится «в один клик». Но и другие модели подойдут.\n\n"
                "Выберите категорию:",
                parse_mode='Markdown',
                reply_markup=markup,
            )

        @self.bot.message_handler(commands=['faq'])
        def send_faq(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            faq_text = """
❓ *Частые вопросы — честно и простыми словами*

*Почему нельзя просто купить VPN за 200 ₽?*
Покупные VPN делят один канал между тысячами людей: скорость падает,
сервера блокируют, компания видит, что и когда вы смотрите.
Ваш сервер — только ваш, быстрый и приватный.

*А бесплатные VPN?*
Бесплатное — это вы. Они торгуют вашими данными, IP меняется по 100 раз в день,
и сайты всё равно не открываются. Свой сервер — стабильно и без сюрпризов.

*Зачем мне разбираться в VPS?* (это и есть «сервер»)
Не надо разбираться. Мы делаем настройку за вас — автоматически.
От вас нужно лишь оплатить покупку коробочки (от 150 ₽/мес) и прислать её адрес.

*Я все равно боюсь/не понимаю, как платить. Поможете?*
Да! Пошаговая простая инструкция → /vps.
Если застряли в любой момент — пишите поддержке: @beliy_obhodchik_support_bot

*Что, если мой интернет-провайдер заблокирует связь с этим сервером?*
Мы используем маскировку Reality — со стороны провайдер видит обычный
безопасный сайт (например, api.notion.com), а не VPN. Плюс мы ведём
автообновление доноров и при случае меняем их без вашего участия.
            """
            self.bot.send_message(message.chat.id, faq_text, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=self._main_menu_inline_keyboard())

        @self.bot.message_handler(commands=['guide', 'instructions', 'howto'])
        def send_guide(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            guide_text = """
📖 *Краткая инструкция — БелыйОбходчик*

1. `/buy` — начать
2. Указать IP сервера (как завести сервер: /vps)
3. Оплатить (1500 ₽ или {stars} ⭐)
4. Бот сам подключается к вашему серверу, ставит и настраивает всё + присылает готовый файл для роутера
   • *Авто-SSH*: прислать пароль `root` (бот сделает всё сам)
   • *Сам*: скачать `setup_vps.sh`, запустить, прислать блок вывода

🎛 *На какие устройства даём готовые настройки:*
{devices}

💡 Не знаете, какой роутер подойдёт? → /router — подберём модель.

🛠 *Что ещё можем настроить за вас (пишите в поддержку):*
{services}

Подробная инструкция: см. [INSTRUCTIONS.md](https://github.com/camce6e-wq/beliy-obhodchik/blob/main/INSTRUCTIONS.md)

Нужна помощь? Напишите: /support (вопрос уйдёт человеку) или поддержке @beliy_obhodchik_support_bot
            """.format(
                stars=STARS_PRICE,
                devices="\n".join(f"• {d}" for d in SUPPORTED_DEVICES),
                services="\n".join(f"• *{name}* — {desc}" for name, desc in ADDITIONAL_SERVICES),
            )
            self.bot.send_message(message.chat.id, guide_text, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=self._main_menu_inline_keyboard())
        
        def _build_status_text(self) -> str:
            """Текст состояния системы: SNI-доноры и лучший донор."""
            stats = self.sni_db.get_stats()
            status_text = f"""
📊 *Статус системы:*

• Всего SNI-доноров: {stats['total']}
• Активных: {stats['active']}
• Средняя успешность: {stats['avg_success_rate']:.2%}

🏆 *Лучшие страны:*
"""
            
            for country, count in list(stats['by_country'].items())[:5]:
                status_text += f"  • {country}: {count} доноров\n"
            
            # Получаем лучший донор
            best_donor = self.sni_db.get_best_donor()
            if best_donor:
                status_text += f"\n✨ *Лучший донор:*\n"
                status_text += f"  • {best_donor['hostname']}\n"
                status_text += f"  • Успешность: {best_donor['success_rate']:.2%}\n"
            
            status_text += "\n✅ Система работает стабильно"
            return status_text
        
        @self.bot.message_handler(commands=['status'])
        def send_status(message):
            """Состояние системы (SNI-доноры). Только для владельцев."""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            if ADMIN_USER_IDS and user_id not in ADMIN_USER_IDS:
                self._show_welcome(message.chat.id)
                return
            self.bot.send_message(message.chat.id, _build_status_text(self), parse_mode='Markdown', reply_markup=self._main_menu_inline_keyboard())
        
        @self.bot.message_handler(commands=['support'])
        def send_support(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            self.user_states[user_id] = "support"
            self.bot.send_message(
                message.chat.id,
                "🎧 *Поддержка*\n\n"
                "Опишите проблему своими словами — я отвечу на типовые вопросы сам, "
                "а если не справлюсь, передам ваш вопрос человеку.\n\n"
                "Например:\n"
                "• «Как оплатить?»\n"
                "• «Перестал работать Ютуб»\n"
                "• «Где взять IP сервера?»\n\n"
                "Чтобы закрыть чат, отправьте: /exit",
                parse_mode='Markdown',
                reply_markup=self._main_menu_inline_keyboard(),
            )

        @self.bot.message_handler(commands=['exit'])
        def exit_support(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self.user_states.get(user_id) == "support":
                self.user_states[user_id] = None
                self.bot.send_message(message.chat.id, "👌 Чат поддержки закрыт.")
            else:
                self.bot.send_message(message.chat.id, "🙂 Вы не в чате поддержки.")
            self._show_welcome(message.chat.id)

        @self.bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
        def handle_support_message(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            # Ответы владельца на пересланные сообщения
            if self._reply_from_owner(message):
                return
            # Перехват только в режиме поддержки
            if self.user_states.get(user_id) != "support":
                return
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return

            answer = self._support_auto_answer(message.text)
            if answer:
                self.bot.send_message(message.chat.id, answer, parse_mode='Markdown')
                return

            # Непонятный вопрос → человеку
            sent = self._forward_to_owner(message)
            message_text = (
                "🧑‍💻 Вопрос сложный — я передал его человеку. "
                "Ответ придёт сюда в этот чат." if sent else
                "😕 Я пока учусь. Обратитесь к человеку: @beliy_obhodchik_support_bot"
            )
            self.bot.send_message(message.chat.id, message_text)

        @self.bot.message_handler(commands=['test'])
        def test_generation(message):
            """Тестовая команда для генерации конфига (только для владельцев)."""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if ADMIN_USER_IDS and user_id not in ADMIN_USER_IDS:
                self._show_welcome(message.chat.id)
                return
            if self._antiflood(user_id, COMMAND_COOLDOWN * 3):
                self._notify_slow(message.chat.id, user_id)
                return
            
            try:
                # Быстрая генерация для теста
                temp_file, params = quick_generate(
                    server_ip="93.184.216.34",  # Тестовый IP
                    sni_hostname="api.notion.com"
                )
                
                with open(temp_file, 'rb') as f:
                    self.bot.send_document(
                        message.chat.id,
                        f,
                        caption=f"""
✅ *Тестовый конфиг сгенерирован:*
• Сервер: {params['server_ip']}
• SNI: {params['sni_hostname']}
• UUID: `{params['uuid']}`
                        """,
                        parse_mode='Markdown'
                    )
                
                # Удаляем временный файл
                os.remove(temp_file)
                
            except Exception as e:
                self.bot.send_message(
                    message.chat.id,
                    f"❌ Ошибка: {str(e)}",
                    reply_markup=self._main_menu_inline_keyboard(),
                )
        
        @self.bot.message_handler(commands=['stats'])
        def admin_stats(message):
            """Статистика (только для администраторов)."""
            if not self._guard_message(message):
                return
            if message.from_user.id not in ADMIN_USER_IDS:
                return
            try:
                conn = sqlite3.connect(self.payment_system.db_path)
                cur = conn.cursor()

                def one(q):
                    return cur.execute(q).fetchone()[0]

                payments_total = one("SELECT COUNT(*) FROM payments")
                payments_paid = one("SELECT COUNT(*) FROM payments WHERE status='paid'")
                payments_pending = one("SELECT COUNT(*) FROM payments WHERE status='pending'")
                orders_total = one("SELECT COUNT(*) FROM orders")
                users_total = one("SELECT COUNT(DISTINCT user_id) FROM payments")
                invoices_active = one("SELECT COUNT(*) FROM invoices WHERE status='active'")
                setups_total = one("SELECT COUNT(*) FROM vps_setups")
                revenue_rub = one("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status='paid' AND (source IS NULL OR source != 'stars')")
                revenue_stars = one("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status='paid' AND source = 'stars'")
                conn.close()
            except Exception as e:
                logger.error("Ошибка /stats: %s", e)
                self.bot.send_message(message.chat.id, f"❌ Ошибка получения статистики: {e}", reply_markup=self._main_menu_inline_keyboard())
                return

            text = (
                "📊 *Статистика:*\n\n"
                f"👤 Пользователей: {users_total}\n"
                f"💰 Платежей всего: {payments_total}\n"
                f"✅ Оплачено: {payments_paid}\n"
                f"⏳ Ожидают оплату: {payments_pending}\n"
                f"📦 Заказов: {orders_total}\n"
                f"🧾 Активных счетов: {invoices_active}\n"
                f"🖥 Установок VPS выполнено: {setups_total}\n"
                f"💳 Выручка (crypto): {revenue_rub} ₽\n"
                f"⭐ Выручка (звёзды): ~{revenue_stars} ⭐\n"
            )
            self.bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=self._main_menu_inline_keyboard())
        
        @self.bot.pre_checkout_query_handler(func=lambda query: True)
        def handle_pre_checkout(query):
            """Подтверждаем платёж звёздами."""
            try:
                self.bot.answer_pre_checkout_query(query.id, ok=True)
            except Exception as e:
                logger.error("Ошибка pre_checkout: %s", e)
                self.bot.answer_pre_checkout_query(query.id, ok=False, error_message=str(e))
        
        @self.bot.message_handler(content_types=["successful_payment"])
        def handle_successful_payment(message):
            """После оплаты звёздами сразу доставляем конфиг.
            Идемпотентно: дубликат уведомления не приводит к повторной выдаче."""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            payload = str(message.successful_payment.invoice_payload)
            stars = message.successful_payment.total_amount // 1

            # ===== Услуга «Без сервера» (обход Ютуб/Дискорд/Инстаграм/ТикТок через Zapret) =====
            if payload.startswith("dpi_"):
                if stars < DPI_STARS_PRICE:
                    self.bot.send_message(
                        message.chat.id,
                        "⚠️ Сумма оплаты не совпадает с ценой настройки. "
                        "Напишите в поддержку: @beliy_obhodchik_support_bot",
                        reply_markup=self._main_menu_inline_keyboard()
                    )
                    return
                with self._lock:
                    try:
                        self.payment_system.mark_payment_stars(payload, DPI_STARS_PRICE)
                        self.payment_system.confirm_payment(payload)
                        if not self._deliver_dpi_setup(message.chat.id, user_id, stars, payment_id=payload):
                            return
                        inv = self.payment_system.get_invoice_by_payment(payload)
                        if inv:
                            self.payment_system.mark_invoice_paid(inv['invoice_id'])
                    except Exception as e:
                        logger.error("Ошибка доставки DPI после оплаты звёздами: %s", e)
                return

            # ===== VPS + звёзды =====
            with self._lock:
                # После рестарта user_data пуст, но счёт со IP/SNI сохранён в БД при pay_stars
                inv = self.payment_system.get_invoice_by_payment(payload)
                ud = self.user_data.get(user_id, {})
                if not inv and ud.get('payment_id') != payload:
                    self.bot.send_message(
                        message.chat.id,
                        f"✅ Оплата {stars} ⭐ получена! Продолжите с /buy или напишите поддержке: @beliy_obhodchik_support_bot",
                        reply_markup=self._main_menu_inline_keyboard()
                    )
                    return

                # Уже доставляли по этому платежу — не дублируем
                if self.payment_system.find_paid_order(payload):
                    self.bot.send_message(
                        message.chat.id,
                        f"✅ Оплата {stars} ⭐ по этому платежу уже была принята ранее. "
                        "Конфиг/скрипт отправлялись выше. Нужен повторно — напишите в /support.",
                        reply_markup=self._main_menu_inline_keyboard()
                    )
                    return

                # Достаём параметры доставки: из счёта в БД или из user_data
                data = {
                    'payment_id': payload,
                    'user_id': user_id,
                    'chat_id': message.chat.id,
                    'server_ip': (inv or ud).get('server_ip') if (inv or ud) else None,
                    'sni_hostname': (inv or ud).get('sni_hostname') if (inv or ud) else None,
                }
                if not data['server_ip'] or not data['sni_hostname']:
                    self.bot.send_message(
                        message.chat.id,
                        f"✅ Оплата {stars} ⭐ получена! Данные сервера не найдены после перезапуска. "
                        "Начните снова с /buy — повторная оплата не потребуется, напишите поддержке: @beliy_obhodchik_support_bot",
                        reply_markup=self._main_menu_inline_keyboard()
                    )
                    return

                try:
                    self.payment_system.mark_payment_stars(payload, STARS_PRICE)
                    self.payment_system.confirm_payment(payload)
                    self.user_data[user_id] = data
                    if not self._offer_install_choice(user_id, message.chat.id):
                        ok = self.generate_and_send_config(user_id, message.chat.id, data=data)
                        if ok:
                            self.bot.send_message(
                                message.chat.id,
                                f"✅ *Оплата {stars} ⭐ подтверждена!* Конфиг сформирован и отправлен выше.",
                                parse_mode='Markdown',
                                reply_markup=self._main_menu_inline_keyboard()
                            )
                        else:
                            self.bot.send_message(
                                message.chat.id,
                                "❌ Не удалось сформировать конфиг. Напишите в поддержку: @beliy_obhodchik_support_bot",
                                reply_markup=self._main_menu_inline_keyboard()
                            )
                    if inv:
                        self.payment_system.mark_invoice_paid(inv['invoice_id'])
                except Exception as e:
                    logger.error("Ошибка доставки после оплаты звёздами: %s", e)
                    self.bot.send_message(
                        message.chat.id,
                        "❌ Произошла ошибка. Платёж получен, напишите в поддержку: @beliy_obhodchik_support_bot",
                        reply_markup=self._main_menu_inline_keyboard()
                    )

        @self.bot.message_handler(func=lambda m: m.text and m.text.startswith('/'))
        def handle_unknown_command(message):
            """Неизвестная команда: не молчим, а показываем меню."""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            command = (message.text or '').split()[0]
            self.bot.send_message(
                message.chat.id,
                f"🤔 Не знаю команду `{self._md_escape(command)}`. Вот что я умею:",
                parse_mode='Markdown',
                reply_markup=self._main_menu_inline_keyboard(),
            )
    
    def get_user_state(self, user_id: int) -> Optional[str]:
        """Получение состояния пользователя"""
        return self.user_states.get(user_id)
    
    def generate_and_send_config(self, user_id: int, chat_id: int,
                                 data: Optional[dict] = None) -> bool:
        """Генерация и отправка конфигурации пользователю.
        data: если передан (например, из фоновой проверки оплаты),
        используется он вместо self.user_data."""
        
        user_data = data if data is not None else self.user_data.get(user_id, {})
        
        if not all(k in user_data for k in ['server_ip', 'sni_hostname', 'payment_id']):
            self.bot.send_message(chat_id, "❌ Ошибка: не все данные собраны.", reply_markup=self._main_menu_inline_keyboard())
            return False
        
        try:
            # Ищем давно установленное VPS-подключение с реальными ключами
            real_setup = None
            try:
                real_setup = self.payment_system.get_vps_setup(user_id)
            except Exception:
                real_setup = None
            
            if real_setup:
                # Реальный конфиг: ключи уже установлены на VPS пользователя
                generator = KeeneticConfigGenerator(
                    server_ip=real_setup['server_ip'],
                    server_port=443,
                    uuid=real_setup['uuid'],
                    sni_hostname=real_setup['sni_hostname'],
                    public_key=real_setup['public_key'],
                    short_id=real_setup['short_id']
                )
                archive_path = generator.create_complete_package()
                params = {
                    'uuid': real_setup['uuid'],
                    'public_key': real_setup['public_key'],
                    'short_id': real_setup['short_id']
                }
            else:
                # Установка ещё не выполнена — генерируем демо-конфиг
                archive_path, params = quick_generate(
                    server_ip=user_data['server_ip'],
                    sni_hostname=user_data['sni_hostname']
                )
            
            # Создаём заказ в базе (идемпотентно: повтор гонки не плодит дубли)
            order_id = self.payment_system.create_order(
                payment_id=user_data['payment_id'],
                user_id=user_id,
                config_path=archive_path,
                server_ip=user_data['server_ip'],
                uuid=params['uuid'],
                sni_hostname=user_data['sni_hostname']
            )
            
            # Отправляем архив пользователю
            with open(archive_path, 'rb') as f:
                self.bot.send_document(
                    chat_id,
                    f,
                    caption=f"""
✅ *Ваш заказ готов!*

📦 *Данные заказа:*
• ID заказа: `{self._md_escape(order_id)}`
• Сервер: {self._md_escape(user_data['server_ip'])}
• SNI донор: `{self._md_escape(user_data['sni_hostname'])}`
• UUID: `{self._md_escape(params['uuid'])}`
• Short ID: `{self._md_escape(params['short_id'])}`

📁 *Архив содержит:*
1. Конфигурация для Xray (`xray_client.json`)
2. Конфигурация для Sing-box (`singbox_client.json`)
3. Правила HydraRoute + CLI-команды
4. VLESS-ссылка + инструкция README.txt

📋 *Что делать дальше:*
1. Распакуйте архив на компьютере
2. Следуйте инструкции README.txt
3. Настройте роутер Keenetic
4. Проверьте работу интернета

💡 *Помощь:*
• Инструкция в архиве
• Поддержка: @beliy_obhodchik_support_bot
• Вопросы: /faq

🔄 *Автообновления:*
При смене SNI-донора система автоматически обновит конфигурацию.
                        """,
                    parse_mode='Markdown'
                )
            
            self.bot.send_message(
                chat_id,
                "Нажмите кнопку меню, если нужно что-то ещё 👇",
                reply_markup=self._main_menu_inline_keyboard()
            )
            
            # Отмечаем заказ как доставленный
            self.payment_system.mark_order_delivered(order_id)
            
            # Сбрасываем состояние пользователя
            with self._lock:
                self.user_states[user_id] = None
                if data is None:
                    self.user_data[user_id] = {}
            
            # Удаляем временный файл
            try:
                os.remove(archive_path)
            except OSError:
                pass
            
            logger.info(f"Конфиг отправлен пользователю {user_id}, заказ {order_id}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при генерации конфига: {e}")
            self.bot.send_message(
                chat_id,
                f"❌ Ошибка при генерации конфигурации: {str(e)}",
                reply_markup=self._main_menu_inline_keyboard()
            )
            return False
    
    def _check_payments_loop(self):
        """Фоновая проверка оплат через Crypto Pay API."""
        while True:
            time.sleep(10)
            if not self.crypto_pay or not self.pending_payments:
                # Подчищаем старые откаты, чтобы словарь не рос бесконечно
                cutoff = time.monotonic() - 3600
                self._delivery_backoff = {
                    k: v for k, v in self._delivery_backoff.items() if v > cutoff
                }
                continue
            try:
                paid = self.crypto_pay.get_paid_invoices()
            except Exception as e:
                logger.warning("Ошибка проверки оплат: %s", e)
                continue
            self._process_paid_invoices(paid)

    def _process_paid_invoices(self, paid: list):
        """Доставка конфигов по оплаченным счетам (крипто).
        Идемпотентность: каждый payment_id берётся из очереди ровно один раз;
        заказы создаются идемпотентно; «paid» ставим только после успешной доставки.
        Сверка суммы: не доставляем конфиг, если пришла другая сумма/валюта."""
        for inv in paid:
            pid = (inv or {}).get('payload') or ''
            if not pid:
                continue
            # Откат после ошибки: не долбим Crypto Bot каждые 10 секунд
            last_fail = self._delivery_backoff.get(pid, 0)
            if time.monotonic() - last_fail < 60:
                continue
            with self._lock:
                if pid not in self.pending_payments:
                    continue
                rec = self.pending_payments.pop(pid, None)
                if not rec:
                    continue
                try:
                    # Сверка суммы и валюты с тем, что мы выставили
                    pay_row = self.payment_system.get_payment(rec['payment_id'])
                    expected = (pay_row or {}).get('amount')
                    got = inv.get('amount')
                    if expected and got and int(got) != int(expected):
                        logger.error(
                            "Сумма не сходится для %s: ожидали %s, пришли %s — доставка отменена",
                            pid, expected, got,
                        )
                        self._delivery_backoff[pid] = time.monotonic()
                        continue
                    if not pay_row:
                        logger.error("Нет платежа в БД для %s", pid)
                        continue

                    self.payment_system.confirm_payment(rec['payment_id'])
                    self.user_data[rec['user_id']] = rec
                    ok = False
                    if str(pid).startswith('dpi_'):
                        ok = self._deliver_dpi_setup(rec['chat_id'], rec['user_id'],
                                                     stars=rec.get('stars'), payment_id=pid)
                    else:
                        if not self._offer_install_choice(rec['user_id'], rec['chat_id']):
                            ok = self.generate_and_send_config(
                                rec['user_id'], rec['chat_id'], data=rec
                            )
                        else:
                            # Показали кнопки установки — деньги получены, товар в работе
                            ok = True
                    if ok:
                        self.payment_system.mark_invoice_paid(inv.get('invoice_id', ''))
                        logger.info("Оплата %s подтверждена, доставка выполнена", pid)
                    else:
                        # Доставка сорвалась — вернём в очередь позже (backoff 60 сек)
                        self.pending_payments[pid] = rec
                        self._delivery_backoff[pid] = time.monotonic()
                except Exception as e:
                    logger.error("Ошибка доставки после оплаты %s: %s", pid, e)
                    self.pending_payments[pid] = rec
                    self._delivery_backoff[pid] = time.monotonic()

    def _offer_install_choice(self, user_id: int, chat_id: int) -> bool:
        """Если реальных ключей ещё нет — предлагаем выбрать способ установки.
        Возвращает True, если показаны кнопки выбора."""
        real_setup = None
        try:
            real_setup = self.payment_system.get_vps_setup(user_id)
        except Exception:
            real_setup = None
        if real_setup:
            return False
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔑 Авто-установка по SSH", callback_data="install_ssh"))
        markup.add(types.InlineKeyboardButton("🔧 Сам запущу скрипт", callback_data="install_self"))
        self.bot.send_message(
            chat_id,
            "🖥️ *Установка Xray на ваш VPS*\n\n"
            "Конфиг будет рабочим, только если на VPS стоят те же ключи, что в конфиге.\n"
            "Выберите способ:",
            parse_mode='Markdown',
            reply_markup=markup,
        )
        return True

    def _parse_vps_output(self, text: str) -> Optional[dict]:
        """Парсинг блока вывода setup_vps.sh:
        ===BELIY-OBHODCHIK-VLESS===
        SERVER_IP=...
        UUID=...
        PUBLIC_KEY=...
        SHORT_ID=...
        SNI_HOSTNAME=...
        SNI_IP=...
        ===BELIY-OBHODCHIK-VLESS-END==="""
        if not text:
            return None
        start = text.find("===BELIY-OBHODCHIK-VLESS===")
        end = text.find("===BELIY-OBHODCHIK-VLESS-END===")
        if start < 0 or end < start:
            return None
        block = text[start:end]
        data = {}
        for line in block.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                data[key.strip()] = value.strip()
        need = {'SERVER_IP', 'UUID', 'PUBLIC_KEY', 'SHORT_ID', 'SNI_HOSTNAME'}
        if not need.issubset(set(data)):
            return None
        # Не принимаем мусор: публичный IPv4, валидный hostname, базовый формат ключей
        if not self._is_valid_public_ipv4(data.get('SERVER_IP', '')):
            return None
        if not self._is_valid_hostname(data.get('SNI_HOSTNAME', '')):
            return None
        if not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                            r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', data.get('UUID', '')):
            return None
        if not re.fullmatch(r'[A-Za-z0-9+/=]{20,120}', data.get('PUBLIC_KEY', '')):
            return None
        if not re.fullmatch(r'[0-9a-fA-F]{4,16}', data.get('SHORT_ID', '').split(':')[0]):
            return None
        return data

    def _save_setup_and_deliver(self, user_id: int, chat_id: int, parsed: dict,
                                sni_hostname: Optional[str] = None,
                                sni_ip: Optional[str] = None,
                                install_method: str = "self") -> bool:
        """Сохранение реальных ключей и доставка настоящего конфига."""
        if not parsed:
            return False
        try:
            self.payment_system.save_vps_setup(
                user_id=user_id,
                server_ip=parsed.get('SERVER_IP'),
                uuid=parsed.get('UUID'),
                public_key=parsed.get('PUBLIC_KEY'),
                short_id=parsed.get('SHORT_ID'),
                sni_hostname=parsed.get('SNI_HOSTNAME') or sni_hostname,
                sni_ip=parsed.get('SNI_IP') or sni_ip,
                install_method=install_method,
            )
        except Exception as e:
            logger.error("Не удалось сохранить установку VPS пользователя %s: %s", user_id, e)
            self.bot.send_message(chat_id, "❌ Не удалось сохранить ключи установки.", reply_markup=self._main_menu_inline_keyboard())
            return False
        self.user_states[user_id] = None
        ok = self.generate_and_send_config(user_id, chat_id)
        if ok:
            self.bot.send_message(
                chat_id,
                "✅ *Готово!* Конфиг собран на настоящих ключах вашего VPS и отправлен выше.",
                parse_mode='Markdown',
                reply_markup=self._main_menu_inline_keyboard()
            )
        return ok

    def _run_ssh_install(self, user_id: int, chat_id: int, password: str, ud: dict):
        """Путь B: подключение по SSH, установка Xray, парсинг ключей."""
        server_ip = ud.get('server_ip')
        sni_hostname = ud.get('sni_hostname')
        sni_ip = ud.get('sni_ip')
        if not server_ip or not sni_hostname:
            self._show_welcome(chat_id)
            self.user_states[user_id] = None
            return
        # Защита от инъекции: только публичный IPv4 и валидный hostname
        if not self._is_valid_public_ipv4(server_ip) or not self._is_valid_hostname(sni_hostname):
            logger.error("SSH-установка отклонена: невалидный IP/SNI (user=%s)", user_id)
            self.bot.send_message(
                chat_id,
                "❌ Невалидный IP сервера или SNI-донор. Начните заказ заново через /buy.",
                reply_markup=self._main_menu_inline_keyboard(),
            )
            self.user_states[user_id] = None
            return
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_vps.sh")
        if not os.path.exists(script_path):
            self.bot.send_message(chat_id, "❌ Скрипт setup_vps.sh не найден на сервере бота.", reply_markup=self._main_menu_inline_keyboard())
            self.user_states[user_id] = None
            return
        cli = None
        try:
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cli.connect(
                hostname=server_ip, port=22, username='root',
                password=password, timeout=25,
                allow_agent=False, look_for_keys=False,
            )
            with cli.open_sftp() as sftp:
                sftp.put(script_path, "/tmp/setup_vps.sh")
            env = self._ssh_env_line(sni_hostname, sni_ip)
            cmd = f"{env} bash /tmp/setup_vps.sh 2>&1"
            stdin, stdout, stderr = cli.exec_command(cmd, timeout=240)
            out = stdout.read().decode('utf-8', errors='replace')
            parsed = self._parse_vps_output(out)
            if not parsed:
                logger.error("SSH-установка не вернула блок параметров. Вывод: %s", out[-600:])
                self.bot.send_message(
                    chat_id,
                    "❌ Установка прошла, но бот не нашёл блок ключей. Скопируйте вывод вручную — "
                    "выберите «Сам запущу скрипт», либо напишите в поддержку.",
                    reply_markup=self._main_menu_inline_keyboard(),
                )
                self.user_states[user_id] = None
                return
            self._save_setup_and_deliver(user_id, chat_id, parsed,
                                         sni_hostname=sni_hostname,
                                         sni_ip=sni_ip,
                                         install_method="ssh")
            # Успех: пароль root дальше не нужен, убираем из памяти
            with self._lock:
                ud.pop('ssh_password', None)
        except paramiko.AuthenticationException as e:
            logger.error("SSH-ошибка авторизации для %s (%s): %s", user_id, server_ip, e)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔁 Я всё включил — попробуй ещё раз", callback_data="retry_ssh_install"))
            self.bot.send_message(
                chat_id,
                "❌ Не удалось авторизоваться по SSH на `root`.\n\n"
                "Чаще всего на VPS вход по паролю для root отключён. "
                "Выполните на сервере эти команды:\n\n"
                "```\n"
                "sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config\n"
                "sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config\n"
                "sudo systemctl restart ssh\n"
                "```\n"
                "Также проверьте, что пароль `root` верный.",
                parse_mode='Markdown',
                reply_markup=markup,
            )
            self.user_states[user_id] = None
        except Exception as e:
            logger.error("SSH-ошибка установки для %s (%s): %s", user_id, server_ip, e)
            self.bot.send_message(
                chat_id,
                f"❌ Не удалось подключиться по SSH: {str(e)[:200]}\n"
                f"Проверьте, что IP верный и включён пароль root для SSH (PermitRootLogin).",
                reply_markup=self._main_menu_inline_keyboard(),
            )
            self.user_states[user_id] = None
        finally:
            if cli is not None:
                try:
                    cli.close()
                except Exception:
                    pass

    @staticmethod
    def _vps_recommendations_text() -> str:
        """Список рекомендуемых VPS с реферальными ссылками и промокодом."""
        lines = []
        for name, country, price, url in VPS_RECOMMENDATIONS:
            label = f"{name} ({country}) - {price}"
            lines.append(f"• [{label}]({url})" if url else f"• {label}")
        if PROMO_CODE:
            lines.append(f"\n🎁 Промокод на скидку: `{PROMO_CODE}`")
        return "\n".join(lines)

    def _sni_refresh_loop(self):
        """Фоновое обновление успешности SNI-доноров.
        Периодически перепроверяет существующих доноров и обновляет success_rate,
        чтобы choose_best_donor всегда давал рабочий домен. Сканирование новых
        подсетей не запускаем: демо-сканер заполняет БД случайными данными."""
        if SNI_CHECK_INTERVAL <= 0:
            return
        while True:
            time.sleep(SNI_CHECK_INTERVAL)
            try:
                for donor in self.sni_db.get_donors_for_check(limit=20):
                    res = self.scanner.check_donor(donor['hostname'], donor['ip_address'])
                    self.sni_db.update_success_rate(
                        donor['id'], res['is_accessible'], res['response_time_ms']
                    )
                    logger.info(
                        "SNI-донор %s (%s): %s",
                        donor['hostname'], donor['ip_address'],
                        "доступен" if res['is_accessible'] else "недоступен",
                    )
            except Exception as e:
                logger.warning("Ошибка фоновой проверки SNI-доноров: %s", e)

    def start(self):
        """Запуск бота (устойчив к сетевым сбоям: поллинг перезапускается сам)."""
        logger.info("Запускаю Telegram-бота...")
        threading.Thread(target=self._check_payments_loop, daemon=True).start()
        threading.Thread(target=self._sni_refresh_loop, daemon=True).start()
        while True:
            try:
                self.bot.infinity_polling(
                    none_stop=True,
                    interval=2,
                    timeout=20,
                    long_polling_timeout=LONG_POLLING_TIMEOUT,
                )
            except KeyboardInterrupt:
                logger.info("Бот остановлен пользователем.")
                return
            except Exception as e:
                logger.error("Поллинг завершился с ошибкой, перезапуск через 10 сек: %s", e)
                time.sleep(10)


def main():
    """Точка входа"""
    
    print("="*60)
    print("TELEGRAM БОТ ДЛЯ АВТОМАТИЧЕСКОЙ ПРОДАЖИ КОНФИГОВ")
    print("="*60)
    
    if CRYPTOPAY_TOKEN:
        print(f"✅ Crypto Pay: подключён")
    else:
        print("⚠️  Crypto Pay: токен не задан (CRYPTOPAY_TOKEN) — оплата будет отключена")
    
    # Создаём и запускаем бота
    try:
        bot = AutoConfigBot(TELEGRAM_BOT_TOKEN, crypto_pay_token=CRYPTOPAY_TOKEN)
        bot.start()
    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        print("\n🛠️  *Устранение неполадок:*")
        print("1. Проверьте токен бота")
        print("2. Установите библиотеки: pip install pyTelegramBotAPI")
        print("3. Проверьте подключение к интернету")


if __name__ == "__main__":
    main()