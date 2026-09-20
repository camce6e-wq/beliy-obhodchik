@echo off
chcp 65001 >nul
echo ========================================
echo  Telegram Бот "БелыйОбходчик"
echo ========================================
echo.

set TELEGRAM_BOT_TOKEN=8852560443:REDACTED_REVOKED_TOKEN

echo [1/2] Проверка токена...
echo Токен установлен: ✅
echo.

echo [2/2] Запуск бота...
echo Нажмите Ctrl+C для остановки
echo.

:loop
python bot_runner.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ⚠️  Бот остановился с ошибкой, перезапуск через 5 секунд...
    timeout /t 5 >nul
    goto loop
)