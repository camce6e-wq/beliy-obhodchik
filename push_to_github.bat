@echo off
chcp 65001 >nul
echo ========================================
echo  Загрузка на GitHub через GitHub Desktop
echo ========================================
echo.

set GITHUB_PATH=C:\Users\BacuJlu4\AppData\Local\GitHubDesktop\app-3.4.11\resources\app\git\usr\bin\git.exe
set REPO_PATH=C:\Users\BacuJlu4\Documents\GitHub\beliy-obhodchik

echo [1/5] Проверка путей...
if exist "%GITHUB_PATH%" (
    echo ✅ Git найден: %GITHUB_PATH%
) else (
    echo ⚠️  Git не найден в стандартном пути
    echo    Попробую использовать системный git...
)

echo.
echo [2/5] Настройка репозитория...
if not exist "%REPO_PATH%" (
    echo Создаю папку репозитория...
    mkdir "%REPO_PATH%"
)

echo.
echo [3/5] Инициализация git...
cd /d "%REPO_PATH%"
git init

echo.
echo [4/5] Добавление remote...
git remote add origin https://github.com/camce6e-wq/beliy-obhodchik.git

echo.
echo [5/5] Копирование файлов и загрузка...
echo Копирую файлы из sni-database...

xcopy "C:\Users\BacuJlu4\.zcode\workspace\default\sni-database\*" "%REPO_PATH%\" /E /I /Y

echo.
echo Добавляю файлы в git...
git add .

echo.
echo Создаю коммит...
git commit -m "Initial commit - БелыйОбходчик система"

echo.
echo Загружаю на GitHub...
git push -u origin main

echo.
echo ========================================
echo  Готово!
echo ========================================
echo.
echo Сайт будет доступен через 2 минуты:
echo https://camce6e-wq.github.io/beliy-obhodchik/
echo.
pause