# Changelog

Все заметные изменения проекта БелыйОбходчик. Формат — по мотивам
[Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — по
[SemVer](https://semver.org/lang/ru/) (до первого релиза изменения идут в
`Unreleased`).

## [Unreleased]

### Added

- `/myorders` пагинация: длинный список разбивается на части по 5 заказов
  (с нумерацией «часть X/Y» и меню на последней).
- Меню «Обновить существующий заказ»: показаны все активные заказы по 6 +
  кнопка «▶️ Показать ещё» вместо жёстких 3.
- CI `smoke.yml`: проверка живого сайта (GitHub Pages) каждые 6 часов,
  на `push` и вручную; бот-проверка `getMe` — при наличии секрета `BOT_TOKEN`.
- CI `tests.yml`: lint расширен с фатальных кодов до `ruff check --select E9,F`
  (синтаксис + неопределённые имена + неиспользуемые импорты), раннер закреплён
  за `ubuntu-24.04`.
- Убраны мёртвые `f`-префиксы и неиспользуемые импорты в тестах.
- `AUTONOMOUS_PLAN.md`, `ASSUMPTIONS.md`, `KNOWN_ISSUES.md` — документация к
  автономному прогону; README — актуальная структура и раздел «Тесты и деплой».

### Fixed

- `/myorders` падал с `400 Bad Request` при наличии заказов: `order_...` c
  подчёркиванием внутри `*...*` в Markdown открывал незакрытый italic
  (`telegram_bot.py`) — id заказа экранируется `_md_escape()`.
- `START_HERE.md`: уточнено, что `/test` доступен только владельцу
  (`ADMIN_USER_IDS`), токены берутся из `.env`.

## [2026-09-27]

### Fixed

- `/faq`, `/guide` и вопрос поддержке падали с `400 can't parse entities`:
  `@beliy_obhodchik_support_bot` (три подчёркивания) открывал незакрытый
  italic в Markdown — адрес экранируется `\_` в `telegram_bot.py`
  (`SUPPORT_ANSWERS`, `faq_text`, `guide_text`, сообщение о доставке конфига).
- Нижняя reply-клавиатура застревала у клиентов после обновления клавиатуры
  меню: приветствие теперь шлёт `ReplyKeyboardRemove` отдельным сообщением.

## [2026-09-26]

### Added

- `sitemap.xml`: `lastmod` 2026-09-27 для всех страниц.
- SEO-тесты в CI — `title`/`description` по длине, `canonical`, `og:title`,
  `og:image`.
- Mini App: тап-таргеты brand/footer ≥ 44px, `og`-теги и `canonical`.

## [2026-09-25]

### Changed

- `/vps` переформатирован под мобильные сообщения (тесты `/guide` и `/faq`).
- Hero лендинга: чип VLESS без наезда на окно, высота контента 128→84px.
- Контраст иконки в featured-карточке приложения.

## [2026-09-24]

### Added

- Telegram Mini App (`webapp/`) + кнопка приложения в меню бота,
  команда `/app`, приветствие по услугам сайта.
- Deep-link маршрутизация `?start=` (vps/dpi/claude/transfer) в боте.
- `404.html`, `canonical` на страницах, файл верификации Search Console.

### Changed

- Редизайн v2: многостраничный сайт (услуги, адаптив, мета для шеринга).
- Редизайн v3: near-black + lime, grain, bento-сетка, моно-лейблы,
  bottom-nav в Mini App.

## [Ранее]

Прототип: одностраничный лендинг + бот, оплата Crypto Pay и Telegram Stars,
генератор конфигов Keenetic, база SNI-доноров, скрипты установки Xray.

---

См. также `git log` для полной истории коммитов.