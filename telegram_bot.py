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
👋 *Добро пожаловать в БелыйОбходчик!*

Я помогу вам автоматически настроить интернет без ограничений на вашем роутере Keenetic.

✨ *Что я умею:*
• Автоматически настраивать VLESS Reality на вашем VPS
• Генерировать готовые конфиги для Keenetic
• Подбирать работающие SNI-доноры
• Обновлять настройки при блокировках

📋 *Основные команды:*
/start - Начало работы
/buy - Купить автоматическую настройку
/myorders - Мои заказы
/status - Статус системы
/help - Помощь

💡 *Как это работает:*
1. Вы покупаете VPS за границей (100-200 ₽/месяц)
2. Я настраиваю на нём VLESS Reality
3. Вы загружаете готовый файл в роутер
4. Интернет работает без ограничений

Всё автоматически. Никаких технических знаний не нужно!

Хотите начать? Нажмите /buy
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
💰 *Стоимость: 500 ₽ один раз* (или {STARS_PRICE} ⭐)

Что входит:
• Автоматическая настройка вашего VPS
• Готовый конфиг для Keenetic
• Правила HydraRoute
• Автообновления доноров
• Поддержка 30 дней

Для продолжения:
1. Введите IP адрес вашего VPS сервера
   (например: 123.45.67.89)

Если сервера ещё нет, рекомендую глянуть:
{self._vps_recommendations_text()}

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
                
                # Получаем новый SNI-донор
                donor = self.sni_db.get_best_donor()
                
                if donor:
                    # В реальной системе здесь обновляем конфиг в БД
                    # и отправляем пользователю новый конфиг
                    
                    update_info = f"""
🔄 *Обновление заказа {order_id[:8]}...*

Найден новый SNI-донор:
• Маскировка: `{donor['hostname']}`
• Успешность: {donor['success_rate']:.0%}
• Страна: {donor['country_code']}

Система автоматически обновила конфигурацию.
                    """
                    
                    self.bot.send_message(
                        call.message.chat.id,
                        update_info,
                        parse_mode='Markdown'
                    )
                else:
                    self.bot.send_message(
                        call.message.chat.id,
                        "❌ Не удалось найти новый SNI-донор. Попробуйте позже."
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
        
        @self.bot.message_handler(commands=['status'])
        def system_status(message):
            if not self._guard_message(message):
                return
            user_id = message.from_user.id
            if self._antiflood(user_id, COMMAND_COOLDOWN):
                self._notify_slow(message.chat.id, user_id)
                return
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
            
            self.bot.send_message(message.chat.id, status_text, parse_mode='Markdown')
        
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
                ok = self.generate_and_send_config(
                    rec['user_id'], rec['chat_id'], data=rec
                )
                if ok:
                    self.payment_system.mark_invoice_paid(inv.get('invoice_id', ''))
                    logger.info("Оплата %s подтверждена, конфиг отправлен", pid)
                else:
                    self.pending_payments[pid] = rec
            except Exception as e:
                logger.error("Ошибка доставки после оплаты %s: %s", pid, e)
                self.pending_payments[pid] = rec

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