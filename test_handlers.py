#!/usr/bin/env python3
"""Офлайн-тест базовых команд основного бота (telegram_bot.py)."""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import telegram_bot as tb

bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN)

captured = []
bot.bot.send_message = lambda chat_id, text, **kw: captured.append(text)
bot.bot.reply_to = lambda m, text, **kw: captured.append(text)
bot.bot.send_document = lambda chat_id, doc, **kw: captured.append("DOC: " + (kw.get('caption') or ''))


class Chat:
    id = 123456789


class User:
    id = 123456789
    username = "test_user"


class Message:
    def __init__(self, text):
        self.chat = Chat()
        self.from_user = User()
        self.text = text
        self.id = 1


def find_command_handler(command):
    for h in bot.bot.message_handlers:
        cmds = h.get('filters', {}).get('commands')
        if cmds and command in cmds:
            return h['function']
    return None


for command in ("start", "status", "test"):
    handler = find_command_handler(command)
    print(f"== /{command} ==")
    if not handler:
        print("ХЕНДЛЕР НЕ НАЙДЕН")
        continue
    captured.clear()
    try:
        handler(Message("/" + command))
    except Exception as e:
        print("ОШИБКА:", repr(e))
        continue
    for text in captured:
        print(text.strip()[:300])
    print()

print("OK-DONE")
