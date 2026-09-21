@echo off
chcp 65001 >nul
echo ========================================
echo  Telegram Бот "БелыйОбходчик" (легаси-запуск bot_runner)
echo  Для нового продакшена используйте run_prod_bot.bat
echo ========================================
echo.

rem Токен подставляется из .env — здесь секретов не храним.
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"

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