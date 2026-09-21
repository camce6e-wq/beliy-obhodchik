#!/usr/bin/env python3
"""
Запуск Telegram-бота для автоматической настройки Keenetic
Единый экземпляр с автоперезапуском при сбое.
"""

import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', "")

print(f"🔧 Использую токен: {TOKEN[:10]}...", flush=True)

import telebot
from telebot import types

# Оборот DPI: запрещаем keep-alive, чтобы каждый запрос к api.telegram.org
# шёл по свежему короткому TCP-соединению (долгие соединения сеть сбрасывает).
import telebot.apihelper as _ah
_orig_session = _ah._get_req_session


def _fresh_session():
    s = _orig_session()
    s.headers['Connection'] = 'close'
    return s


_ah._get_req_session = _fresh_session

bot = telebot.TeleBot(TOKEN, parse_mode=None)


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = """
👋 *Добро пожаловать в БелыйОбходчик!*

Я помогу автоматически настроить интернет без ограничений на вашем роутере Keenetic.

✨ *Что я умею:*
• Автоматически настраивать VLESS Reality на вашем VPS
• Генерировать готовые конфиги для Keenetic
• Подбирать работающие SNI-доноров
• Обновлять настройки при блокировках

📋 *Основные команды:*
/start - Начало работы
/test - Тестовая генерация конфига
/status - Статус системы
/help - Помощь

💡 *Как это работает:*
1. Вы покупаете VPS за границей (100-200 ₽/месяц)
2. Я настраиваю на нём VLESS Reality
3. Вы загружаете готовый файл в роутер
4. Интернет работает без ограничений

💰 *Стоимость:* 500 ₽ один раз за автоматическую настройку
    """
    bot.reply_to(message, welcome_text, parse_mode='Markdown')


@bot.message_handler(commands=['test'])
def test_generation(message):
    from keenetic_config_generator import quick_generate
    bot.reply_to(message, "🔄 Генерирую тестовый конфиг...")
    try:
        temp_file, params = quick_generate(
            server_ip="93.184.216.34",
            sni_hostname="api.notion.com"
        )
        with open(temp_file, 'rb') as f:
            bot.send_document(
                message.chat.id,
                f,
                caption=f"""
✅ *Тестовый конфиг сгенерирован:*
• Сервер: {params['server_ip']}
• SNI: {params['sni_hostname']}
• UUID: `{params['uuid']}`
• Short ID: `{params['short_id']}`
                """,
                parse_mode='Markdown'
            )
        os.remove(temp_file)
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")


@bot.message_handler(commands=['status'])
def system_status(message):
    try:
        from sni_manager import SNIDatabase
        db = SNIDatabase()
        stats = db.get_stats()
        status_text = f"""
📊 *Статус системы:*
• Всего SNI-доноров: {stats['total']}
• Активных: {stats['active']}
• Средняя успешность: {stats['avg_success_rate']:.2%}
✅ *Система работает нормально*
🤖 *Бот активен с:* 20.09.2026
        """
        bot.reply_to(message, status_text, parse_mode='Markdown')
    except Exception:
        bot.reply_to(message, "✅ Система работает. База SNI-доноров в разработке.")


@bot.message_handler(func=lambda message: True)
def echo_all(message):
    if message.text:
        bot.reply_to(message, f"🤖 Я бот для автоматической настройки Keenetic.\n/start - Инструкция\n/test - Тест\n/status - Статус")


MAX_RETRIES = 5
RETRY_DELAY = 5


def run():
    attempt = 0
    while True:
        attempt += 1
        try:
            print("🔄 Проверяю подключение к Telegram API...", flush=True)
            bot_info = bot.get_me()
            print(f"✅ Бот найден: @{bot_info.username} ({bot_info.first_name})", flush=True)
            print("🚀 Бот запущен! Напишите /start в Telegram", flush=True)
            print("⏸️  Нажмите Ctrl+C для остановки", flush=True)
            bot.infinity_polling(none_stop=True, interval=2, timeout=20, long_polling_timeout=0)
            # infinity_polling не возвращает управление, пока бот не остановлен
            break
        except KeyboardInterrupt:
            print("🛑 Остановлен по Ctrl+C", flush=True)
            break
        except Exception as e:
            print(f"❌ Попытка {attempt}/{MAX_RETRIES} — Ошибка: {e}", flush=True)
            if attempt >= MAX_RETRIES:
                print("💀 Все попытки исчерпаны. Бот не запущен.", flush=True)
                print("🛠️ Попробуйте запустить вручную: python bot_runner.py", flush=True)
                sys.exit(1)
            print(f"🔄 Перезапуск через {RETRY_DELAY} сек...", flush=True)
            time.sleep(RETRY_DELAY)


if __name__ == "__main__":
    run()