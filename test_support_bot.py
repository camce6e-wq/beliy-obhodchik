#!/usr/bin/env python3
"""Офлайн-тест отдельного бота поддержки (support_bot.py)."""
import os
import sys
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import support_bot as sb

ADMIN = 6002841224
USER = 555111222

bot = sb.SupportBot('111:AAAA', admin_ids=[ADMIN])


def make_message(text, user_id=USER, msg_id=1000, chat_id=999000,
                 chat_type='private', reply_to=None):
    msg = SimpleNamespace()
    msg.text = text
    msg.message_id = msg_id
    msg.chat = SimpleNamespace(id=chat_id, type=chat_type)
    msg.from_user = SimpleNamespace(id=user_id, username=f"u{user_id}")
    msg.reply_to_message = reply_to
    return msg


captured = []
bot.bot.send_message = lambda chat_id, text, **kw: captured.append(text)
bot.bot.send_document = lambda *a, **k: captured.append("DOC")
bot.bot.forward_message = lambda admin_id, from_chat_id, message_id: SimpleNamespace(
    message_id=100 + message_id)


def call_by_filter(attr, predicate):
    for h in getattr(bot.bot, attr):
        if predicate(h.get('filters', {})):
            h['function'](make_message(""), )
            return
    raise AssertionError(f"не найден хендлер по {attr}")


def call_command(text, **kw):
    msg = make_message(text, **kw)
    for h in bot.bot.message_handlers:
        cmds = h.get('filters', {}).get('commands')
        if cmds and msg.text.strip().lstrip('/').split()[0] in cmds:
            h['function'](msg)
            return msg
    raise AssertionError(f"команда не найдена: {text}")


def call_text(msg):
    for h in bot.bot.message_handlers:
        if not h.get('filters', {}).get('commands') and ('func' in h.get('filters', {})):
            h['function'](msg)
            return
    raise AssertionError("text-хендлер не найден")


def reset():
    captured.clear()
    bot.last_cmd.clear()
    bot.forwards.clear()


# 1) Автоответчик повторяет правила основного бота
CASES = [("как оплатить?", "Оплата"), ("Ютуб не работает", "Не работает"),
         ("хочу человека", "Передаю ваш вопрос"), ("лдлваолдыоа", None)]
for text, expect in CASES:
    a = sb.auto_answer(text)
    good = (expect in (a or "")) if expect else (a is None)
    assert good, f"auto_answer({text!r}) -> {a!r}"
    print(f"OK auto_answer {text!r} => {('OK ' if good else 'FAIL')}")

# 2) /start приветствие
reset()
call_command("/start")
assert any("Поддержка" in c for c in captured), captured
print(f"OK /start -> {len(captured)} сообщение(я)")

# 3) Типовой вопрос: автоответ, без пересылки
reset()
call_text(make_message("как оплатить?"))
join = "\n".join(captured)
assert "Оплата" in join and not bot.forwards, captured
print("OK автоответ без пересылки")

# 4) Сложный вопрос: пересылка владельцу + сообщение пользователю
reset()
call_text(make_message("апфдлоываждлоапд", msg_id=3000))
join = "\n".join(captured)
assert "передал его человеку" in join, captured
assert len(bot.forwards) == 1, bot.forwards
print("OK сложный вопрос переслан владельцу, юзеру сказано")

# 5) Ответ владельца (reply) доставляется пользователю
captured.clear()
bot.last_cmd.clear()
fwd_id = next(iter(bot.forwards))
reply_msg = make_message("Да, поможем. Напишите IP.", user_id=ADMIN, msg_id=9001,
                         chat_id=ADMIN, reply_to=SimpleNamespace(message_id=fwd_id))
call_text(reply_msg)
join = "\n".join(captured)
assert "Ответ поддержки" in join, captured
assert "Ответ отправлен" in join, captured
print("OK ответ владельца доставлен юзеру")

# 6) Сообщение владельца без reply игнорируется
reset()
call_text(make_message("просто заметка", user_id=ADMIN, msg_id=9002, chat_id=ADMIN))
assert not captured, captured
print("OK сообщение владельца без reply не обрабатывается")

# 7) Не личный чат -> отказ
reset()
call_text(make_message("как оплатить?", chat_type='group'))
join = "\n".join(captured)
assert "только в личных" in join, captured
print("OK групповой чат отклонён")

# 8) Антифлуд: второе сообщение подряд не пересылается, а говорит "подождите"
reset()
call_text(make_message("вопрос один", msg_id=3101))
assert len(bot.forwards) == 1, bot.forwards
call_text(make_message("вопрос два подряд", msg_id=3102))
join = "\n".join(captured)
assert "Не так быстро" in join, captured
assert len(bot.forwards) == 1, "флуд не должен пересылать повторно"
print("OK антифлуд работает (пересылка единственная)")

print()
print("OK-DONE")
sys.exit(0)