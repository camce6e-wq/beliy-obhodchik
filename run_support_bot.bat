@echo off
rem Запуск бота поддержки (support_bot.py) с секретами из .env
setlocal
if not exist ".env" (
    echo .env не найден. Скопируйте .env.example в .env и заполните токены.
    exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
echo Запуск бота поддержки...
python support_bot.py