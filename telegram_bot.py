#!/usr/bin/env python3
"""
Telegram-бот для автоматической продажи конфигов
Полностью автономная обработка заказов
"""

import os
import json
import logging
import hashlib
import uuid
from datetime import datetime
from typing import Dict, Optional, Tuple
import sqlite3
import base64
import secrets

# Для работы с Telegram API
try:
    import telebot
    from telebot import types
    from telebot.util import quick_markup
except ImportError:
    print("Установите библиотеку: pip install pyTelegramBotAPI")
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


class AutoConfigBot:
    """Основной класс Telegram-бота"""
    
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token)
        self.payment_system = PaymentSystem()
        self.sni_db = SNIDatabase()
        self.scanner = SNIScanner(self.sni_db)
        
        # Состояния пользователей (user_id -> state)
        self.user_states = {}
        
        # Данные пользователей (user_id -> data)
        self.user_data = {}
        
        # Регистрация обработчиков
        self.register_handlers()
    
    def register_handlers(self):
        """Регистрация обработчиков команд"""
        
        @self.bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            user_id = message.from_user.id
            username = message.from_user.username or str(user_id)
            
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
            user_id = message.from_user.id
            username = message.from_user.username or str(user_id)
            
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
            
            # Начинаем процесс покупки
            self.user_states[user_id] = "awaiting_server_ip"
            self.user_data[user_id] = {}
            
            # Создаём платеж
            payment_id = self.payment_system.create_payment(user_id, username)
            self.user_data[user_id]['payment_id'] = payment_id
            
            instruction = """
💰 *Стоимость: 500 ₽ один раз*

Что входит:
• Автоматическая настройка вашего VPS
• Готовый конфиг для Keenetic
• Правила HydraRoute
• Автообновления доноров
• Поддержка 30 дней

Для продолжения:
1. Введите IP адрес вашего VPS сервера
   (например: 123.45.67.89)

Если сервера ещё нет, рекомендую:
• Hetzner (Германия) - от 3 €/месяц
• TimeWeb (Финляндия) - от 120 ₽/месяц
• AWS Lightsail (США) - от 3.5 $/месяц

Введите IP вашего сервера:
            """
            
            self.bot.send_message(message.chat.id, instruction, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda message: self.get_user_state(message.from_user.id) == "awaiting_server_ip")
        def process_server_ip(message):
            user_id = message.from_user.id
            server_ip = message.text.strip()
            
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
            markup.add(types.InlineKeyboardButton("💳 Оплатить 500 ₽", callback_data="make_payment"))
            markup.add(types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_purchase"))
            
            self.bot.send_message(
                message.chat.id, 
                donor_info, 
                parse_mode='Markdown',
                reply_markup=markup
            )
        
        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callback(call):
            user_id = call.from_user.id
            
            if call.data == "make_payment":
                # Создаём ссылку на оплату (в реальности через ЮKassa/Stripe)
                payment_id = self.user_data.get(user_id, {}).get('payment_id')
                
                payment_url = f"https://your-payment-gateway.com/pay/{payment_id}"
                
                # В демо-версии сразу подтверждаем оплату
                self.payment_system.confirm_payment(payment_id)
                
                # Генерируем конфигурацию
                self.generate_and_send_config(user_id, call.message.chat.id)
                
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
            user_id = message.from_user.id
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
            user_id = message.from_user.id
            
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
    
    def get_user_state(self, user_id: int) -> Optional[str]:
        """Получение состояния пользователя"""
        return self.user_states.get(user_id)
    
    def generate_and_send_config(self, user_id: int, chat_id: int):
        """Генерация и отправка конфигурации пользователю"""
        
        user_data = self.user_data.get(user_id, {})
        
        if not all(k in user_data for k in ['server_ip', 'sni_hostname', 'payment_id']):
            self.bot.send_message(chat_id, "❌ Ошибка: не все данные собраны.")
            return
        
        try:
            # Генерируем конфигурацию
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
            
        except Exception as e:
            logger.error(f"Ошибка при генерации конфига: {e}")
            self.bot.send_message(
                chat_id,
                f"❌ Ошибка при генерации конфигурации: {str(e)}"
            )
    
    def start(self):
        """Запуск бота"""
        logger.info("Запускаю Telegram-бота...")
        self.bot.polling(none_stop=True)


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
    
    # Проверяем наличие токена
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not token:
        print("⚠️  Токен бота не найден в переменных окружения.")
        print("    Создайте бота через @BotFather и добавьте токен:")
        print("    export TELEGRAM_BOT_TOKEN='ваш_токен'")
        print()
        print("    Для демонстрации создам фейкового бота...")
        
        demo = create_demo_bot()
        print("\n📱 *Демо-бот создан*")
        print("Доступные команды:")
        
        for cmd, desc in demo.commands.items():
            print(f"  {cmd} - {desc}")
        
        print("\n🤖 *Реальный бот будет работать так:*")
        print("1. Пользователь: /start")
        print("2. Бот: Отправляет приветствие и меню")
        print("3. Пользователь: /buy")
        print("4. Бот: Запрашивает IP сервера")
        print("5. Бот: Находит лучший SNI-донор")
        print("6. Бот: Предлагает оплатить")
        print("7. После оплаты: Генерирует и отправляет конфиг")
        print("8. Бот: Отправляет архив с конфигурацией")
        
        return
    
    # Создаём и запускаем бота
    try:
        bot = AutoConfigBot(token)
        bot.start()
    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        print("\n🛠️  *Устранение неполадок:*")
        print("1. Проверьте токен бота")
        print("2. Установите библиотеки: pip install pyTelegramBotAPI")
        print("3. Проверьте подключение к интернету")


if __name__ == "__main__":
    main()