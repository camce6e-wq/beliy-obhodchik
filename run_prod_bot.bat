@echo off
rem Запуск основного бота (telegram_bot.py) с секретами из .env
setlocal
if not exist ".env" (
    echo .env не найден. Скопируйте .env.example в .env и заполните токены.
    exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
echo Запуск телеграм-бота...
python telegram_bot.py