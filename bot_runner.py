#!/usr/bin/env python3
"""
Простой запуск Telegram-бота для тестирования
"""

import os
import sys

# Устанавливаем токен
TOKEN = "8852560443:REDACTED_REVOKED_TOKEN"
os.environ['TELEGRAM_BOT_TOKEN'] = TOKEN

print(f"🔧 Использую токен: {TOKEN[:10]}...")
print("Запускаю бота...")

# Пробуем импортировать и запустить бота
try:
    import telebot
    from telebot import types
    
    # Создаём бота
    bot = telebot.TeleBot(TOKEN, parse_mode=None)
    
    # Проверяем подключение
    print("🔄 Проверяю подключение к Telegram API...")
    bot_info = bot.get_me()
    print(f"✅ Бот найден: @{bot_info.username} ({bot_info.first_name})")
    
    # Простые обработчики
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        welcome_text = """
👋 *Добро пожаловать в БелыйОбходчик!*

Я помогу автоматически настроить интернет без ограничений на вашем роутере Keenetic.

✨ *Что я умею:*
• Автоматически настраивать VLESS Reality на вашем VPS
• Генерировать готовые конфиги для Keenetic
• Подбирать работающие SNI-доноры
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
        """Тестовая генерация конфига"""
        from keenetic_config_generator import quick_generate
        import tempfile
        
        bot.reply_to(message, "🔄 Генерирую тестовый конфиг...")
        
        try:
            # Быстрая генерация для теста
            temp_file, params = quick_generate(
                server_ip="93.184.216.34",  # Тестовый IP (example.com)
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

📁 *Архив содержит:*
1. Конфигурация для Xray/Sing-box
2. Правила HydraRoute
3. Инструкция по настройке
4. Скрипт проверки обновлений

💡 *Тестовая версия* — для реального использования укажите IP вашего VPS.
                    """,
                    parse_mode='Markdown'
                )
            
            # Удаляем временный файл
            import os
            os.remove(temp_file)
            
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка при генерации: {str(e)}")
    
    @bot.message_handler(commands=['status'])
    def system_status(message):
        """Статус системы"""
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
👥 *Пользователей:* 1 (тест)
            """
            
            bot.reply_to(message, status_text, parse_mode='Markdown')
        except:
            bot.reply_to(message, "✅ Система работает. База SNI-доноров в разработке.")
    
    @bot.message_handler(func=lambda message: True)
    def echo_all(message):
        """Ответ на любое сообщение"""
        if message.text:
            bot.reply_to(message, f"🤖 Я бот для автоматической настройки Keenetic. Используйте команды:\n/start - Инструкция\n/test - Тест\n/status - Статус")
    
    print("🚀 Бот запущен! Напишите /start в Telegram")
    print("⏸️  Нажмите Ctrl+C для остановки")
    
    # Запускаем бота
    bot.polling(none_stop=True)
    
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("Установите библиотеку: pip install pyTelegramBotAPI")
except Exception as e:
    print(f"❌ Ошибка: {e}")
    print("\n🛠️  Устранение неполадок:")
    print("1. Проверьте токен бота (должен быть без пробелов)")
    print("2. Проверьте подключение к интернету")
    print("3. Проверьте что бот создан через @BotFather")
    print(f"4. Текущий токен: {TOKEN}")