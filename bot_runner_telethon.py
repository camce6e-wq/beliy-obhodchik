#!/usr/bin/env python3
"""
Telegram бот для автоматической настройки Keenetic через Telethon (поддерживает прокси)
"""

import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Токен бота (теперь не нужен для Telethon) — берём из .env, без хардкода
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', "")
os.environ['TELEGRAM_BOT_TOKEN'] = TELEGRAM_BOT_TOKEN

print(f"🔧 Использую токен: {os.environ['TELEGRAM_BOT_TOKEN'][:10]}...")
print("Запускаю бота через Telethon...")

try:
    from telethon import TelegramClient, events, functions, types
    import asyncio
    import json

    API_ID = 6  # Временно
    API_HASH = "42b5185a6c5349449364c0c2dbcbd6a1"  # Временно (нужен настоящий)

    print(f"📱 API_ID: {API_ID}")
    print(f"🔐 API_HASH: {API_HASH[:10]}...")

    # Создаем клиента
    client = TelegramClient('bot_session', API_ID, API_HASH)

    async def start_bot():
        """Запуск бота"""

        # Активируем бота
        try:
            await client(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(),
                lang_code='ru',
                commands=[
                    types.BotCommand(command='start', description='Начало работы'),
                    types.BotCommand(command='help', description='Помощь'),
                    types.BotCommand(command='test', description='Тестовая генерация'),
                    types.BotCommand(command='status', description='Статус'),
                ]
            ))
            print("✅ Команды бота активированы")
        except Exception as e:
            print(f"⚠️  Не удалось установить команды: {e}")

        print("🚀 Бот запущен! Напишите /start в Telegram")
        print("⏸️  Нажмите Ctrl+C для остановки\n")

        @client.on(events.NewMessage)
        async def handle_message(event):
            """Обработчик новых сообщений"""
            text = event.message.text or ""

            # Команда /start
            if text == '/start':
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
                await event.reply(welcome_text, parse_mode='markdown')

            # Команда /test
            elif text == '/test':
                try:
                    from keenetic_config_generator import quick_generate
                    import tempfile

                    await event.reply("🔄 Генерирую тестовый конфиг...")

                    temp_file, params = quick_generate(
                        server_ip="93.184.216.34",
                        sni_hostname="api.notion.com"
                    )

                    with open(temp_file, 'rb') as f:
                        await event.reply_document(
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
                            parse_mode='markdown'
                        )

                    import os
                    os.remove(temp_file)

                except Exception as e:
                    await event.reply(f"❌ Ошибка при генерации: {str(e)}")

            # Команда /status
            elif text == '/status':
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
                    await event.reply(status_text, parse_mode='markdown')
                except:
                    await event.reply("✅ Система работает. База SNI-доноров в разработке.")

            # Любое другое сообщение
            else:
                await event.reply(f"🤖 Я бот для автоматической настройки Keenetic. Используйте команды:\n/start - Инструкция\n/test - Тест\n/status - Статус")

        # Запускаем бота
        await client.start()
        await client.run_until_disconnected()

    # Запускаем
    async def main():
        await client.connect()
        try:
            # Попытка запуска через токен бота
            await client.start(bot_token=os.environ['TELEGRAM_BOT_TOKEN'])
            await start_bot()
        except Exception as e:
            print(f"❌ Ошибка авторизации: {e}")
            print("Проверьте API_ID, API_HASH и токен бота")
            print("\n💡 Для получения API_ID и API_HASH:")
            print("1. Перейдите на https://my.telegram.org")
            print("2. Авторизуйтесь")
            print("3. Раздел 'API development tools'")
            print("4. Получите API_ID и API_HASH")
        finally:
            await client.disconnect()

    asyncio.run(main())

except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("Установите библиотеку: pip install telethon")
except Exception as e:
    print(f"❌ Ошибка: {e}")
    print("\n🛠️  Устранение неполадок:")
    print("1. Проверьте API_ID и API_HASH в @MyTelegramBot")
    print("2. Проверьте подключение к интернету")
    print("3. Если есть блокировки Telegram, используйте VPN/Tor")
