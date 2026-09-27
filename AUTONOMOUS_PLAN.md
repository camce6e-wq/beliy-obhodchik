# Autonomous Plan — БелыйОбходчик (2026-09-27)

Автономный прогон «Finish Project End-End». Без вопросов пользователю;
предполечения — в `ASSUMPTIONS.md`, невыполнимое — в `KNOWN_ISSUES.md`.

## Baseline (Discovery, шаг 1 — выполнено)

- HEAD `ae200e5`, дерево чистое; CI `tests` + Pages зелёные; VPS-сервисы active.
- Все 13 тест-файлов + `tests/test_site_pages.py` + `tests/test_contrast.py` + `_check.py` — PASS
  (один флейк `test_menu_flow.py` → найден и починен прод-баг `/myorders`, см. P0).
- Секреты: `.env` не в git, `.gitignore` покрывает `.env/*.db/*.log`.
- Копии сайта (Default Project ↔ репо) идентичны по содержимому (разница только EOL).
- TODO/FIXME в репо нет; GitHub issues пусты.

## Inventory (чек-лист по приоритету)

### P0 — блокеры (баги продакшена)

- [x] `/myorders` → Telegram 400: `*Заказ order_xxx...*` — `_` внутри `*bold*` открывает
  незакрытый italic (реальный прод-баг, флаки в тестах зависели от наличия заказов в
  `payments.db`). Фикс: `self._md_escape(...)` в `telegram_bot.py:1752`.
  Проверка: `test_menu_flow.py` ×5 подряд — зелёный.

### P1 — тесты и CI

- [x] CI (`tests.yml`) не запускает `tests/test_contrast.py` — добавлен в py_compile- и run-списки.
- [x] В CI нет линтера — добавлен шаг `ruff check --select E9,F63,F7,F82`
      (только фатальные-класс ошибок, без style-churn, без конфиг-файла в репо).

### P2 — документация (missão: README с актуальными командами)

- [x] `README.md`: устаревший блок структуры (`sni-database/` в корне, нет
  `webapp/`, `tests/`, `deploy/`, `instructions/`) + нет раздела «Тесты и деплой» — обновлён.
- [x] `START_HERE.md`: `/test` — admin-only, не «тестовый конфиг для всех»; токен из `.env`, не
  `set TELEGRAM_BOT_TOKEN` — поправлено.
- [x] `CHANGELOG.md` — создан (Kept-a-Changelog + conventional commits).
- [x] `ASSUMPTIONS.md` — создан (где линтер был, что заменяет typecheck, две копии сайта,
  smoke-ограничения бота).
- [x] `KNOWN_ISSUES.md` — создан (сводный список известных ограничений).

### P3 — верификация и деплой (шаг 4)

- [x] Полный прогон всех сьютов ×2 (детерминизм) — зелёные.
- [x] Build-check: `python -m py_compile` всех `*.py` + `sh -n`/`bash -n` скриптов (как CI).
- [x] Lint: `ruff check --select E9,F63,F7,F82` по репо — чисто.
- [x] Security: ECC `security-audit` (dependencies+secrets) — критических нет.
- [x] Smoke сайта: `http.server` + fetch ключевых URL (/, service-*, 404, webapp/, css, img) — 200.
- [x] Живой сайт — 200 по всем ключевым URL.
- [x] Push → CI зелёный; VPS `deploy/update.sh` → оба сервиса active, 400-ых не прибавилось.
- [x] Деплой сайта (`deploy_site.ps1`) — НЕ требовался: веб-файлы не менялись.

### P4 — Delivery (шаг 5)

- [x] Conventional commits: `fix(bot)`, `test(ci)`, `docs`.
- [x] Финальный отчёт `=== AUTONOMOUS RUN COMPLETE ===` в чате.

## Verification Gate (команды)

```powershell
# все тесты (поштучно, как CI)
python test_menu_flow.py && python test_landing_html.py && ... (14 файлов)
# build + shell
python -m compileall -q .
# lint
ruff check --select E9,F63,F7,F82 .
# site
python _check.py ; python tests\test_site_pages.py ; python tests\test_contrast.py
```
