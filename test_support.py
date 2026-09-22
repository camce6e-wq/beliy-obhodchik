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

print()
print("OK-DONE" if ok else "ЕСТЬ ПАДЕНИЯ")
sys.exit(0 if ok else 1)