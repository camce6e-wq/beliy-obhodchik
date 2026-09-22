#!/usr/bin/env python3
"""Офлайн-тест чата поддержки (автоответчик по ключевым словам)."""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import telegram_bot as tb

bot = tb.AutoConfigBot('111:AAAA')

CASES = [
    ("как оплатить?", "оплата"),
    ("Ютуб не работает вообще", "problem"),
    ("где взять IP сервера?", "ip"),
    ("как купить сервер", "vps"),
    ("как загрузить файл в роутер", "router"),
    ("ошибка при установке ssh", "install"),
    ("хочу поговорить с человеком", "human"),
    ("привет!", "hello"),
    ("спасибо большое", "thanks"),
    ("лдлваолдыоаыо", None),
]

ok = True
for text, expected_topic in CASES:
    answer = bot._support_auto_answer(text)
    if expected_topic is None:
        good = answer is None
        label = "unrecognized -> None"
    else:
        good = answer is not None and "TESTSHOULDNOTHAVE" not in answer
        label = f"{expected_topic} <-> answer-found:{answer is not None}"
    result = "OK " if good else "FAIL"
    if not good:
        ok = False
    print(f"{result} {text!r} => {label}" + (" (no answer)" if answer is None and expected_topic is not None else ""))

for text in ("аплодлаплод", "йцукенфывап"):
    # убеждаемся, что неизвестное возвращает None
    assert bot._support_auto_answer(text) is None, f"ожидали None для {text!r}"

# Приоритет тем: специфичное должно выигрывать у общего (install раньше ip/vps проверяется).
# Например "ssh" -> install, а не problem.
a = bot._support_auto_answer("ssh не работает")
assert a and "Установка Xray" in a, "ssh должен определяться как install"

# "купить сервер" -> vps, но слово 'купить' есть и в оплате? проверяем что попал vps.
a = bot._support_auto_answer("как купить сервер")
assert a and "Про сервер" in a, "'купить сервер' должно определяться как vps"

# Подбор роутера: специфичные модели определяются до общих правил.
router_cases = [
    ("у меня keenetic giga", "Keenetic"),
    ("daKeenetic Omni", "Keenetic"),
    ("роутер tp-link archer", "TP-Link"),
    ("xiaomi redmi ax3000", "Xiaomi"),
]
for text, brand in router_cases:
    a = bot._support_auto_answer(text)
    assert a and brand in a, f"для {text!r} ожидали ответ про {brand}, получили: {(a or '')[:80]!r}"
    print(f"OK router {text!r} => {brand}")

# Каталог подбора роутеров доступен и содержит нужные категории
assert set(("budget", "mid", "premium", "keenetic_family")).issubset(tb.ROUTER_CATEGORIES)
for key, (title, _, models) in tb.ROUTER_CATEGORIES.items():
    assert title and models, f"категория {key} пустая"
    for m in models:
        assert len(m) == 3, f"модель в {key} должна быть (имя, цена, фичи): {m}"
print("OK каталог роутеров: 4 категории и модели валидны")

# Команда /router зарегистрирована
from telegram_bot import AutoConfigBot
bots = [h for h in bot.bot.message_handlers if h.get('filters', {}).get('commands')]
assert any("router" in h['filters']['commands'] for h in bots), "/router не зарегистрирован"
print("OK /router зарегистрирован")

# Callback router_* отдаёт осмысленный текст с моделями
captured = []
bot.bot.send_message = lambda chat_id, text, **kw: captured.append(text)


class RCall:
    class From:
        id = 123456789
        username = "tester"
    from_user = From()

    class Msg:
        class Chat:
            id = 123456789
            type = 'private'
        chat = Chat()
    message = Msg()


for cb in ("router_budget", "router_mid", "router_premium", "router_keenetic_family"):
    captured.clear()
    bot.last_cmd.clear()
    call = RCall()
    call.data = cb
    bot.bot.answer_callback_query = lambda *a, **k: None
    for h in bot.bot.callback_query_handlers:
        h['function'](call)
    joined = "\n".join(captured)
    assert joined and ("Бюджетный" in joined or "Средний" in joined or "Мощный" in joined or "Keenetic" in joined), f"{cb} пустой"
    assert "Кейнет" not in joined
    print(f"OK callback {cb} -> {len(captured)} сообщение(я)")

print()
print("OK-DONE" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)