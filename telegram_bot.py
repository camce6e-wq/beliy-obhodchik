#!/usr/bin/env python3
"""
Telegram-бот для автоматической продажи конфигов
Полностью автономная обработка заказов
"""

import os
import sys
import json
import time
import logging
import hashlib
import uuid
import threading
from datetime import datetime
from typing import Dict, Optional, Tuple, Any
import sqlite3
import base64
import secrets

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Для работы с Telegram API
try:
    import telebot
    from telebot import types
    from telebot.util import quick_markup
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
from vps_installer import VPSInstaller

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

# Токены берём из окружения (.env). Плейсхолдер ниже — НЕ настоящий секрет,
# он нужен только для офлайн-тестов; продакшен работает через run_prod_bot.bat.
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

# Промокод на скидку для рекомендуемых площадок (пусто = не показывать).
PROMO_CODE = "BELOBH"

# Оплата звёздами Telegram: цена в звёздах за настройку.
# ~250⭐: покупателю ~450-650₽ (в зависимости от канала), боту на вывод ~$3.25.
STARS_PRICE = 250

# ===== Защита бота от спама и злоупотреблений =====
# Администраторы (id через запятую): ADMIN_USER_IDS=6002841224,...
ADMIN_USER_IDS = set(
    int(x.strip()) for x in os.environ.get('ADMIN_USER_IDS', '').split(',') if x.strip().isdigit()
)
# Пауза между командами/кликами одного пользователя (секунды)
COMMAND_COOLDOWN = float(os.environ.get('COMMAND_COOLDOWN', '2'))
CALLBACK_COOLDOWN = float(os.environ.get('CALLBACK_COOLDOWN', '2'))
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

class PaymentSystem:
    """Класс для обработки платежей (упрощённая версия)"""
    
    def __init__(self, db_path: str = "payments.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы платежей"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    username TEXT,
                    amount INTEGER,
                    currency TEXT DEFAULT 'RUB',
                    status TEXT DEFAULT 'pending',
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
            
            conn.commit()
    
    def create_payment(self, user_id: int, username: str, amount: int = 500) -> str:
        """Создание нового платежа"""
        payment_id = f"pay_{hashlib.md5(f'{user_id}{datetime.now()}{secrets.token_hex(4)}'.encode()).hexdigest()[:16]}"
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO payments (payment_id, user_id, username, amount, status)
                VALUES (?, ?, ?, ?, 'pending')
            """, (payment_id, user_id, username, amount))
            
            conn.commit()
        
        logger.info(f"Создан платёж {payment_id} для пользователя {username} ({user_id})")
        return payment_id
    
    def confirm_payment(self, payment_id: str):
        """Подтверждение оплаты"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE payments 
                SET status = 'paid', paid_at = CURRENT_TIMESTAMP
                WHERE payment_id = ?
            """, (payment_id,))
            
            conn.commit()
        
        logger.info(f"Платёж {payment_id} подтверждён")
    
    def create_order(self, payment_id: str, user_id: int, 
                     config_path: str, server_ip: str, 
                     uuid: str, sni_hostname: str) -> str:
        """Создание заказа"""
        order_id = f"order_{hashlib.md5(f'{payment_id}{datetime.now()}'.encode()).hexdigest()[:12]}"
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orders (order_id, payment_id, user_id, config_path, server_ip, uuid, sni_hostname)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (order_id, payment_id, user_id, config_path, server_ip, uuid, sni_hostname))
            
            conn.commit()
        
        logger.info(f"Создан заказ {order_id} для платежа {payment_id}")
        return order_id
    
    def get_user_orders(self, user_id: int) -> list:
        """Получение заказов пользователя"""
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM vps_setups WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_order(self, order_id: str) -> Optional[dict]:
        """Получение одного заказа по order_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_vps_setup_sni(self, user_id: int, sni_hostname: str,
                             sni_ip: Optional[str] = None) -> bool:
        """Смена SNI-донора в сохранённой установке (ключи VPS не меняются)."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE vps_setups SET sni_hostname = ?, sni_ip = ? WHERE user_id = ?",
                (sni_hostname, sni_ip, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def create_invoice_record(self, invoice_id: str, payment_id: str, user_id: int,
                              chat_id: int, server_ip: Optional[str],
                              sni_hostname: Optional[str], pay_url: str):
        """Сохраняем созданный счёт (для проверки оплаты после перезапуска)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO invoices
                (invoice_id, payment_id, user_id, chat_id, server_ip, sni_hostname, status, pay_url)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            """, (invoice_id, payment_id, user_id, chat_id, server_ip, sni_hostname, pay_url))
            conn.commit()

    def get_active_invoices(self) -> dict:
        """Все ожидающие оплаты счета: payment_id -> данные"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM invoices WHERE status = 'active'
            """).fetchall()
            return {r['payment_id']: dict(r) for r in rows}

    def mark_invoice_paid(self, invoice_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE invoices SET status = 'paid' WHERE invoice_id = ?", (invoice_id,))
            conn.commit()


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

    def create_invoice(self, amount_rub: int = 500, payload: str = "",
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
        """Счета со статусом paid. API возвращает {"items": [...]}."""
        res = self._post('getInvoices', {'status': 'paid', 'count': 100})
        if isinstance(res, dict):
            return res.get('items') or []
        return res or []


class AutoConfigBot:
    """Основной класс Telegram-бота"""
    
    def __init__(self, token: str, crypto_pay_token: str = ""):
        self.bot = telebot.TeleBot(token)
        self.payment_system = PaymentSystem()
        self.sni_db = SNIDatabase()
        self.scanner = SNIScanner(self.sni_db)
        self.crypto_pay = CryptoPayClient(crypto_pay_token) if crypto_pay_token else None

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

    def _active_pending_for(self, user_id: int) -> Optional[str]:
        """payment_id активного (неоплаченного) счёта пользователя или None."""
        for pid, row in self.payment_system.get_active_invoices().items():
            if row.get('user_id') == user_id:
                return pid
        return None

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
            "• Крипта (Crypto Bot): нажмите кнопку «Оплатить 500 ₽» в боте — "
            "откроется платёж в USDT, оплачиваете картой/криптой.\n"
            "• Звёзды: кнопка «⭐ Оплатить звёздами» — берётся из баланса Telegram "
            "через приложение на телефоне.\n\n"
            "Оплата разовая, 500 ₽ или 250 ⭐, за саму настройку. "
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
            "🎛 *Как загрузить настройки в роутер:*\n"
            "После установки бот пришлёт ZIP-архив. Загружаете его в роутер "
            "(или импортируете конфиг) — и всё работает у всей семьи.\n\n"
            "Поддерживаемые устройства: Keenetic, ASUS, OpenWrt, Xiaomi, TP-Link, "
            "Android, iPhone, ПК. Полный список и инструкция: /guide\n\n"
            "Не нашли ваш роутер? Напишите «не мой роутер» — подберём вариант."
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
            "Сейчас наберёт поддержка @beliy_obhodchik_support. "
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
        for keywords, topic in self.SUPPORT_RULES:
            if any(kw in tl for kw in keywords):
                return self.SUPPORT_ANSWERS[topic]
        return None

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
            return True
        except Exception as e:
            logger.error("Не удалось доставить ответ пользователю %s: %s", user_id, e)
            self.bot.send_message(message.chat.id, f"❌ Не удалось доставить ответ (id {user_id}).")
            return True

    def register_handlers(self):
        """Регистрация обработчиков команд"""
        
        @self.bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            username = message.from_user.username or str(user_id)
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
            
            welcome_text = """
👋 *Здравствуйте! Я — БелыйОбходчик.*

Понимаю вашу боль без технических слов:

❌ *Сейчас:* Ютуб не грузится, видео «крутится» часами, сайты не открываются, приложения падают.

✅ *Вы хотите:* чтобы всё работало как раньше — и вы не думали, *как* это устроено.

➡️ *Что нужно от вас (один раз):*
1. Купить крошечный «сервер-коробочку» за границей — от 150 ₽/мес (для сравнения: одна поездка на маршрутке). По шагам покажем → /vps
2. Оплатить настройку: 500 ₽ или 250 ⭐

➡️ *Что мы сделаем (дальше всё само):*
• Подключимся к вашему серверу и настроим его автоматически
• Соберём готовые настройки прямо для вашего роутера (Keenetic, и др.)
• При блокировках обновим донора сами
• Поддержка 30 дней

🎁 *Почему это лучше, чем «купить VPN за 200₽»:* наш сервер принадлежит *вам* — высокая скорость, личный не забитый IP, без падений и слежки. Подробно: /faq

Начать просто: нажмите → /buy
            """
            
            self.bot.reply_to(message, welcome_text, parse_mode='Markdown')
        
        @self.bot.message_handler(commands=['buy'])
        def start_purchase(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            username = message.from_user.username or str(user_id)
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
                    )
                return

            # Начинаем процесс покупки
            self.user_states[user_id] = "awaiting_server_ip"
            self.user_data[user_id] = {}
            
            # Создаём платеж
            payment_id = self.payment_system.create_payment(user_id, username)
            self.user_data[user_id]['payment_id'] = payment_id
            
            instruction = f"""
💰 *Стоимость настройки: 500 ₽ один раз* или {STARS_PRICE} ⭐

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
            
            # Простая валидация IP
            import re
            ip_pattern = r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'
            
            if not re.match(ip_pattern, server_ip):
                self.bot.send_message(
                    message.chat.id,
                    "❌ Неверный формат IP адреса. Введите правильный IP (например: 123.45.67.89):"
                )
                return
            
            self.user_data[user_id]['server_ip'] = server_ip
            self.user_states[user_id] = "awaiting_payment"
            
            # Получаем лучший SNI-донор
            donor = self.sni_db.get_best_donor()
            
            if not donor:
                self.bot.send_message(
                    message.chat.id,
                    "❌ Временно нет доступных SNI-доноров. Попробуйте позже."
                )
                self.user_states[user_id] = None
                return
            
            self.user_data[user_id]['sni_hostname'] = donor['hostname']
            self.user_data[user_id]['sni_ip'] = donor['ip_address']
            
            # Показываем информацию о доноре
            donor_info = f"""
✅ Найден рабочий SNI-донор:
• Маскировка: `{donor['hostname']}`
• Страна: {donor['country_code']}
• Успешность: {donor['success_rate']:.0%}

Теперь можно оплатить настройку.
            """
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("💳 Оплатить 500 ₽ (Crypto Bot)", callback_data="make_payment"))
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
                    "Запустите setup_vps.sh на VPS и пришлите сюда весь вывод целиком."
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
            if self._antiflood(user_id, CALLBACK_COOLDOWN):
                self._notify_slow(call.message.chat.id, user_id)
                return
            
            if call.data == "make_payment":
                # Создаём счёт в Crypto Bot
                payment_id = self.user_data.get(user_id, {}).get('payment_id')
                if not payment_id:
                    self.bot.send_message(call.message.chat.id, "❌ Данные заказа потеряны. Наберите /buy заново.")
                    return

                if not self.crypto_pay:
                    self.bot.send_message(
                        call.message.chat.id,
                        "⚠️ Оплата временно недоступна. Обратитесь в поддержку: @beliy_obhodchik_support"
                    )
                    return

                # Антиспам: максимум активных счетов на пользователя
                if self._pending_over_limit(user_id):
                    if not self._resume_pending(call.message.chat.id, user_id):
                        self.bot.send_message(
                            call.message.chat.id,
                            "⏳ У вас уже есть активный счёт. Оплатите его и повторите попытку.",
                        )
                    return

                try:
                    ud = self.user_data.get(user_id, {})
                    invoice = self.crypto_pay.create_invoice(
                        amount_rub=500,
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
                        "💳 *Счёт создан на 500 ₽* (оплата в USDT через Crypto Bot)\n\n"
                        "Нажмите кнопку ниже и оплатите. Конфиг придёт автоматически сразу после оплаты.",
                        parse_mode='Markdown',
                        reply_markup=markup,
                    )
                except Exception as e:
                    logger.error("Ошибка создания счёта: %s", e)
                    self.bot.send_message(
                        call.message.chat.id,
                        f"❌ Не удалось создать счёт. Попробуйте позже или напишите в поддержку: @beliy_obhodchik_support\n({str(e)[:120]})"
                    )
                
            elif call.data == "pay_stars":
                payment_id = self.user_data.get(user_id, {}).get('payment_id')
                if not payment_id:
                    self.bot.send_message(call.message.chat.id, "❌ Данные заказа потеряны. Наберите /buy заново.")
                    return
                if self._pending_over_limit(user_id):
                    if not self._resume_pending(call.message.chat.id, user_id):
                        self.bot.send_message(
                            call.message.chat.id,
                            "⏳ У вас уже есть активный счёт. Оплатите его и повторите попытку.",
                        )
                    return
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
                    self.bot.send_message(call.message.chat.id, "❌ Данные заказа потеряны. Наберите /buy заново.")
                    return
                self.user_states[user_id] = "awaiting_self_output"
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_vps.sh")
                if not os.path.exists(script_path):
                    self.bot.send_message(call.message.chat.id, "❌ Скрипт setup_vps.sh не найден на сервере бота.")
                    return
                with open(script_path, 'rb') as f:
                    self.bot.send_document(
                        call.message.chat.id, f,
                        caption=(
                            f"🔧 *Способ 1: сами запустите скрипт*\n\n"
                            f"1. Скачайте и загрузите `setup_vps.sh` на свой VPS\n"
                            f"2. Выполните:\n"
                            f"`chmod +x setup_vps.sh`\n"
                            f"`sudo SNI_HOSTNAME={sni_hostname} bash setup_vps.sh`\n"
                            + (f"Или с указанием IP донора:\n`sudo SNI_HOSTNAME={sni_hostname} SNI_IP={sni_ip} bash setup_vps.sh`\n\n" if sni_ip else "")
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
                    self.bot.send_message(call.message.chat.id, "❌ IP сервера не указан. Наберите /buy заново.")
                    return
                if paramiko is None:
                    self.bot.send_message(call.message.chat.id, "❌ SSH-модуль не установлен. Пока выберите «сам запущу скрипт».")
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
                    self.bot.send_message(call.message.chat.id, "❌ Данные заказа потеряны. Наберите /buy заново.")
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
                self.bot.send_message(call.message.chat.id, "❌ Заказ отменён.")
                self.user_states[user_id] = None
                self.user_data[user_id] = {}
                
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
            
            elif call.data.startswith("update_"):
                order_id = call.data.replace("update_", "")
                order = self.payment_system.get_order(order_id)
                if not order:
                    self.bot.send_message(call.message.chat.id, "❌ Заказ не найден. Наберите /myorders.")
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
                    self.bot.send_message(call.message.chat.id, "❌ Не удалось найти новый SNI-донор. Попробуйте позже.")
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
                    f"🔄 Обновляю конфиг на новый SNI-донор: `{donor['hostname']}` "
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
                        f"• Новый донор: `{donor['hostname']}`\n"
                        f"• Страна: {donor['country_code']}\n"
                        f"• Успешность: {donor['success_rate']:.0%}\n\n"
                        f"Загрузите новый архив в роутер Keenetic (вместо старого).",
                        parse_mode='Markdown',
                    )
                else:
                    self.bot.send_message(
                        call.message.chat.id,
                        "❌ Не удалось сформировать конфиг. Напишите в поддержку: @beliy_obhodchik_support",
                    )
            
            elif call.data == "create_new":
                self.user_states[user_id] = None
                self.user_data[user_id] = {}
                self.bot.send_message(
                    call.message.chat.id,
                    "Отправьте команду /buy для создания нового заказа."
                )
        
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
                self.bot.send_message(message.chat.id, "📭 У вас пока нет заказов.")
                return
            
            response = "📋 *Ваши заказы:*\n\n"
            
            for order in orders:
                status_emoji = "✅" if order['payment_status'] == 'paid' else "⏳"
                delivered_emoji = "📨" if order['delivered_at'] else "📭"
                
                response += f"""
{status_emoji} *Заказ {order['order_id'][:8]}...*
• Сервер: {order['server_ip']}
• SNI: `{order['sni_hostname']}`
• Статус оплаты: {order['payment_status']}
• Создан: {order['created_at'][:10]}
• Доставлен: {delivered_emoji}
                """
            
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
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

*Выбирайте сервер поближе:* {self._vps_recommendations_text()}

🧭 *Мини-инструкция (4 шага, ~1 минута):*
1. Откройте ссылку провайдера выше и нажмите «Заказать сервер»
2. Выберите *страну поближе к вам* (например, Россия или Нидерланды).
   Параметры не важны — маленькие и дешёвые отлично подходят.
3. Придумайте *пароль* (любой, отправьте этот же пароль боту при установке).
   Оплатите картой или криптой — от 150 ₽/мес.
4. Дождитесь письма с IP-адресом и *пришлите этот IP сюда* (4 числа через точки), например `123.45.67.89` — и жмите /buy.

Подробная картинками-инструкция: /guide
            """
            self.bot.send_message(message.chat.id, vps_text, parse_mode='Markdown', disable_web_page_preview=True)
        
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
Если застряли в любой момент — пишите поддержке: @beliy_obhodchik_support

*Что, если мой интернет-провайдер заблокирует связь с этим сервером?*
Мы используем маскировку Reality — со стороны провайдер видит обычный
безопасный сайт (например, api.notion.com), а не VPN. Плюс мы ведём
автообновление доноров и при случае меняем их без вашего участия.
            """
            self.bot.send_message(message.chat.id, faq_text, parse_mode='Markdown', disable_web_page_preview=True)

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
3. Оплатить (500 ₽ или {stars} ⭐)
4. Бот сам подключается к вашему серверу, ставит и настраивает всё + присылает готовый файл для роутера
   • *Авто-SSH*: прислать пароль `root` (бот сделает всё сам)
   • *Сам*: скачать `setup_vps.sh`, запустить, прислать блок вывода

🎛 *На какие устройства даём готовые настройки:*
{devices}

🛠 *Что ещё можем настроить за вас (пишите в поддержку):*
{services}

Подробная инструкция: см. [INSTRUCTIONS.md](https://github.com/camce6e-wq/beliy-obhodchik/blob/main/INSTRUCTIONS.md)

Нужна помощь? @beliy_obhodchik_support
            """.format(
                stars=STARS_PRICE,
                devices="\n".join(f"• {d}" for d in SUPPORTED_DEVICES),
                services="\n".join(f"• *{name}* — {desc}" for name, desc in ADDITIONAL_SERVICES),
            )
            self.bot.send_message(message.chat.id, guide_text, parse_mode='Markdown', disable_web_page_preview=True)
            
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
            
            self.bot.send_message(message.chat.id, status_text, parse_mode='Markdown')
        
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
                parse_mode='Markdown'
            )

        @self.bot.message_handler(commands=['exit'])
        def exit_support(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self.user_states.get(user_id) == "support":
                self.user_states[user_id] = None
                self.bot.send_message(message.chat.id, "👌 Чат поддержки закрыт. Если что — снова пишите /support")
            else:
                self.bot.send_message(message.chat.id, "🙂 Вы сейчас не в чате поддержки. Начать: /support")

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
                "😕 Я пока учусь. Обратитесь к человеку: @beliy_obhodchik_support"
            )
            self.bot.send_message(message.chat.id, message_text)

        @self.bot.message_handler(commands=['test'])
        def test_generation(message):
            """Тестовая команда для генерации конфига"""
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
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
                    f"❌ Ошибка: {str(e)}"
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
                revenue_rub = one("SELECT COUNT(*) FROM payments WHERE status='paid'") * 500
                stars_paid = one("SELECT COUNT(*) FROM payments WHERE status='paid'")  # заглушка
                revenue_stars = one("SELECT COUNT(*) FROM orders WHERE delivered_at IS NOT NULL") * STARS_PRICE
                conn.close()
            except Exception as e:
                logger.error("Ошибка /stats: %s", e)
                self.bot.send_message(message.chat.id, f"❌ Ошибка получения статистики: {e}")
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
            self.bot.send_message(message.chat.id, text, parse_mode='Markdown')
        
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
            """После оплаты звёздами сразу доставляем конфиг."""
            user_id = message.from_user.id
            payload = message.successful_payment.invoice_payload
            stars = message.successful_payment.total_amount // 1
            
            ud = self.user_data.get(user_id, {})
            if ud.get('payment_id') != payload:
                self.bot.send_message(
                    message.chat.id,
                    f"✅ Оплата {stars} ⭐ получена! Обратитесь к поддержке: @beliy_obhodchik_support"
                )
                return
            
            try:
                self.payment_system.confirm_payment(payload)
                if not self._offer_install_choice(user_id, message.chat.id):
                    ok = self.generate_and_send_config(user_id, message.chat.id)
                    if ok:
                        self.bot.send_message(
                            message.chat.id,
                            f"✅ *Оплата {stars} ⭐ подтверждена!* Конфиг сформирован и отправлен выше.",
                            parse_mode='Markdown'
                        )
                    else:
                        self.bot.send_message(
                            message.chat.id,
                            "❌ Не удалось сформировать конфиг. Напишите в поддержку: @beliy_obhodchik_support"
                        )
            except Exception as e:
                logger.error("Ошибка доставки после оплаты звёздами: %s", e)
                self.bot.send_message(
                    message.chat.id,
                    "❌ Произошла ошибка. Платеж получен, напишите в поддержку: @beliy_obhodchik_support"
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
            self.bot.send_message(chat_id, "❌ Ошибка: не все данные собраны.")
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
            
            # Создаём заказ в базе
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
• ID заказа: `{order_id}`
• Сервер: {user_data['server_ip']}
• SNI донор: `{user_data['sni_hostname']}`
• UUID: `{params['uuid']}`
• Short ID: `{params['short_id']}`

📁 *Архив содержит:*
1. Конфигурация для Xray/Sing-box
2. Правила HydraRoute  
3. Инструкция по настройке
4. Скрипт проверки обновлений

📋 *Что делать дальше:*
1. Распакуйте архив на компьютере
2. Следуйте инструкции README.txt
3. Настройте роутер Keenetic
4. Проверьте работу интернета

💡 *Помощь:*
• Инструкция в архиве
• Поддержка: @beliy_obhodchik_support
• FAQ: https://ваш-сайт.ru/faq

🔄 *Автообновления:*
При смене SNI-донора система автоматически обновит конфигурацию.
                        """,
                    parse_mode='Markdown'
                )
            
            # Отмечаем заказ как доставленный
            self.payment_system.mark_order_delivered(order_id)
            
            # Сбрасываем состояние пользователя
            self.user_states[user_id] = None
            self.user_data[user_id] = {}
            
            # Удаляем временный файл
            os.remove(archive_path)
            
            logger.info(f"Конфиг отправлен пользователю {user_id}, заказ {order_id}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при генерации конфига: {e}")
            self.bot.send_message(
                chat_id,
                f"❌ Ошибка при генерации конфигурации: {str(e)}"
            )
            return False
    
    def _check_payments_loop(self):
        """Фоновая проверка оплат через Crypto Pay API."""
        while True:
            time.sleep(10)
            if not self.crypto_pay or not self.pending_payments:
                continue
            try:
                paid = self.crypto_pay.get_paid_invoices()
            except Exception as e:
                logger.warning("Ошибка проверки оплат: %s", e)
                continue
            self._process_paid_invoices(paid)

    def _process_paid_invoices(self, paid: list):
        """Доставка конфигов по оплаченным счетам."""
        for inv in paid:
            pid = (inv or {}).get('payload') or ''
            rec = self.pending_payments.pop(pid, None)
            if not rec:
                continue
            try:
                self.payment_system.confirm_payment(rec['payment_id'])
                self.user_data[rec['user_id']] = rec
                if not self._offer_install_choice(rec['user_id'], rec['chat_id']):
                    ok = self.generate_and_send_config(
                        rec['user_id'], rec['chat_id'], data=rec
                    )
                    if not ok:
                        self.pending_payments[pid] = rec
                self.payment_system.mark_invoice_paid(inv.get('invoice_id', ''))
                logger.info("Оплата %s подтверждена, конфиг отправлен", pid)
            except Exception as e:
                logger.error("Ошибка доставки после оплаты %s: %s", pid, e)
                self.pending_payments[pid] = rec

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
            self.bot.send_message(chat_id, "❌ Не удалось сохранить ключи установки.")
            return False
        self.user_states[user_id] = None
        ok = self.generate_and_send_config(user_id, chat_id)
        if ok:
            self.bot.send_message(
                chat_id,
                "✅ *Готово!* Конфиг собран на настоящих ключах вашего VPS и отправлен выше.",
                parse_mode='Markdown'
            )
        return ok

    def _run_ssh_install(self, user_id: int, chat_id: int, password: str, ud: dict):
        """Путь B: подключение по SSH, установка Xray, парсинг ключей."""
        server_ip = ud.get('server_ip')
        sni_hostname = ud.get('sni_hostname')
        sni_ip = ud.get('sni_ip')
        if not server_ip or not sni_hostname:
            self.bot.send_message(chat_id, "❌ IP сервера или SNI-донор потеряны. Наберите /buy заново.")
            self.user_states[user_id] = None
            return
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_vps.sh")
        if not os.path.exists(script_path):
            self.bot.send_message(chat_id, "❌ Скрипт setup_vps.sh не найден на сервере бота.")
            self.user_states[user_id] = None
            return
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
            env = f"export SNI_HOSTNAME={sni_hostname} SNI_IP={sni_ip or ''}\n"
            cmd = f"{env} bash /tmp/setup_vps.sh 2>&1"
            stdin, stdout, stderr = cli.exec_command(cmd, timeout=240)
            out = stdout.read().decode('utf-8', errors='replace')
            cli.close()
            parsed = self._parse_vps_output(out)
            if not parsed:
                logger.error("SSH-установка не вернула блок параметров. Вывод: %s", out[-600:])
                self.bot.send_message(
                    chat_id,
                    "❌ Установка прошла, но бот не нашёл блок ключей. Скопируйте вывод вручную — "
                    "выберите «Сам запущу скрипт», либо напишите в поддержку."
                )
                self.user_states[user_id] = None
                return
            self._save_setup_and_deliver(user_id, chat_id, parsed,
                                         sni_hostname=sni_hostname,
                                         sni_ip=sni_ip,
                                         install_method="ssh")
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
                f"Проверьте, что IP верный и включён пароль root для SSH (PermitRootLogin)."
            )
            self.user_states[user_id] = None

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

    def start(self):
        """Запуск бота"""
        logger.info("Запускаю Telegram-бота...")
        threading.Thread(target=self._check_payments_loop, daemon=True).start()
        self.bot.infinity_polling(none_stop=True, interval=2, timeout=20, long_polling_timeout=LONG_POLLING_TIMEOUT)


def create_demo_bot():
    """Создание демо-версии бота"""
    
    # Создаём фейкового бота для демонстрации
    class DemoBot:
        def __init__(self):
            self.commands = {
                '/start': 'Начало работы',
                '/buy': 'Купить автоматическую настройку',
                '/myorders': 'Мои заказы',
                '/status': 'Статус системы',
                '/test': 'Тестовая генерация',
                '/help': 'Помощь'
            }
    
    return DemoBot()


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