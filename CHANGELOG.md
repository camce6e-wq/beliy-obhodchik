# Changelog

Все заметные изменения проекта БелыйОбходчик. Формат — по мотивам
[Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — по
[SemVer](https://semver.org/lang/ru/) (до первого релиза изменения идут в
`Unreleased`).

## [Unreleased]

### Added

- `order.html` — страница заказа и оплаты в USDT (TRC-20) без Telegram: выбор
  тарифа, сумма по курсу `open.er-api.com` (при сбое — константа с пометкой
  «резерв»), проверка входящей транзакции через TronGrid прямо в браузере,
  автопроверка раз в 10 секунд до 10 минут; тариф и сумма фиксируются в
  localStorage. Ссылка «Оплата» в шапке и подвале `index.html`, страница
  добавлена в `PAGES` теста `tests/test_site_pages.py`.
- Выдача доступа после оплаты без сервера (`order.html`, «Шаг 2»): тариф DPI —
  ссылка на `setup_nfqws.sh` и шаги установки, тариф VPS — вставка вывода
  `setup_vps.sh` и сборка архива конфигов прямо в браузере
  (`js/config_generator.js`, порт `keenetic_config_generator.py`); факт оплаты
  запоминается в localStorage и восстанавливается при возврате на страницу.
  Паритетный тест `test_config_generator_js.py` сверяет содержимое JS-архива с
  генератором бота и добавлен в CI.
- `pay-usdt.html` — «Как оплатить USDT (TRC-20)»: пошаговая инструкция, что
  такое txid, частые ошибки (другая сеть, не та сумма, платёж не находится).
  Связка: ссылка из `order.html` и FAQ `index.html` (видимый + JSON-LD),
  страница в `PAGES`, `sitemap.xml`, `smoke.yml` и `deploy_site.ps1`.
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

### Changed

- Покупательские CTA ведут на `order.html`, а не в Telegram: кнопки шапки,
  героя и липкие CTA на главной, `service-full.html` и `service-dpi.html`
  (тексты без «в боте/в Telegram»). Telegram остался для вопросов, поддержки,
  подвалов и услуг без тарифа в `order.html` (Claude, перенос).
- `deploy_site.ps1`: `order.html` и `js/` добавлены в списки копирования,
  коммита и живых проверок.
- Deep-link `order.html?tariff=dpi` подставляет тариф со страницы услуги
  (4 ссылки в `service-dpi.html`); параметр не перетирает уже оплаченный
  тариф и съедается из URL после применения. Тест ссылок учёл query-часть href.
- `smoke.yml` следит за воронкой заказа (`order.html`, `js/config_generator.js`,
  `setup_nfqws.sh`); README — `order.html`, `js/` и JS-тест в структуре и списке
  тестов.

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