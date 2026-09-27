#!/usr/bin/env python3
"""Офлайн-тест кнопочного меню: /start -> inline-кнопки, cmd_* диспетчер,
отмена и «пустой экран» -> возврат к приветствию."""
import sys
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import telegram_bot as tb

# Тест не должен зависеть от локального .env (там может быть ADMIN_USER_IDS)
tb.ADMIN_USER_IDS = []

bot = tb.AutoConfigBot(tb.TELEGRAM_BOT_TOKEN)

sent = []
md_texts = []


def fake_send_message(chat_id, text, **kw):
    sent.append({"chat_id": chat_id, "text": text, "markup": kw.get("reply_markup"),
                 "parse_mode": kw.get("parse_mode")})
    if kw.get("parse_mode") == "Markdown":
        md_texts.append(text)
    return SimpleNamespace(message_id=1)


def fake_answer_callback_query(callback_query_id):
    pass


class FakeBot:
    def __init__(self, real):
        self.real = real

    def send_message(self, chat_id, text, **kw):
        return fake_send_message(chat_id, text, **kw)

    def answer_callback_query(self, qid):
        fake_answer_callback_query(qid)

    def __getattr__(self, name):
        return getattr(self.real, name)


bot.bot = FakeBot(bot.bot)

USER = 777
CHAT = 888


def msg(text="/start", user=USER, chat=CHAT):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user),
        chat=SimpleNamespace(id=chat, type='private'),
        message_id=1,
        text=text,
    )


def call(data, user=USER, chat=CHAT):
    return SimpleNamespace(
        id="cbq_1",
        data=data,
        from_user=SimpleNamespace(id=user),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat, type='private')),
    )


# 1) /start показывает приветствие с inline-кнопками главного меню
sent.clear()
bot.last_cmd.clear()
start_handler = None
for h in bot.bot.message_handlers:
    cmds = h.get("filters", {}).get("commands")
    if isinstance(cmds, list) and "start" in cmds:
        start_handler = h["function"]
        break
assert start_handler, "хендлер /start не найден"
start_handler(msg("/start"))
assert len(sent) == 2, "приветствие = сообщение с ReplyKeyboardRemove + сообщение меню"
m = sent[0]
assert "БелыйОбходчик" in m["text"]
assert "/buy" not in m["text"], "в приветствии не должно быть ссылок на команды"
kb0 = m["markup"]
assert kb0 is not None and isinstance(kb0, tb.types.ReplyKeyboardRemove), \
    "первое сообщение должно снимать нижнюю reply-клавиатуру"
kb = sent[1]["markup"]
assert kb is not None, "приветствие должно быть с inline-кнопками"
assert kb.to_dict().get("inline_keyboard"), "это должны быть inline-кнопки"
print(f"OK 1/5: /start -> приветствие+remove + {sum(len(r) for r in kb.to_dict()['inline_keyboard'])} inline-кнопок")

# 2) cmd_* диспетчер: «Купить настройку» -> инструкция с ценами -> ввод IP -> кнопки оплаты
sent.clear()
bot.last_cmd.clear()
cb_handler = [h for h in bot.bot.callback_query_handlers if 'function' in h][-1]['function']
cb_handler(call("cmd_buy"))
assert sent, "cmd_buy должен что-то отправить"
text = " ".join(s["text"] for s in sent)
assert "1500" in text and "750" in text, "цены VPS 1500₽/750⭐ должны быть в тексте"
print("OK 2/6: cmd_buy -> инструкция с ценами 1500₽/750⭐")

sent.clear()
bot.last_cmd.clear()
process_ip = None
for h in bot.bot.message_handlers:
    if h["function"].__name__ == "process_server_ip":
        process_ip = h["function"]
        break
assert process_ip, "хендлер ввода IP не найден"
process_ip(msg("95.217.1.1"))
assert sent, "после ввода IP должны быть кнопки оплаты"
if not any(s["markup"] for s in sent):
    print("SENT:", [s["text"][:80] for s in sent])
assert any(s["markup"] for s in sent), "должны быть кнопки оплаты"
print("OK 3/6: ввод IP -> кнопки оплаты")

# 4) Отмена заказа -> сразу возврат к приветствию
sent.clear()
bot.last_cmd.clear()
cb_handler(call("cancel_purchase"))
assert len(sent) == 2, "после отмены — приветствие + меню"
assert "БелыйОбходчик" in sent[0]["text"], "после отмены показывается приветствие"
assert "Отменён" not in sent[0]["text"], "не должно быть текста про отмену"
assert sent[1]["markup"] and sent[1]["markup"].to_dict().get("inline_keyboard")
print("OK 4/6: отмена -> приветствие")

# 5) cmd_menu -> приветствие (кнопка «В главное меню» в /router)
sent.clear()
bot.last_cmd.clear()
cb_handler(call("cmd_menu"))
assert len(sent) == 2 and "БелыйОбходчик" in sent[0]["text"]
assert sent[1]["markup"] and sent[1]["markup"].to_dict().get("inline_keyboard")
print("OK 5/6: cmd_menu -> приветствие")

# 6) Все кнопки меню ведут на реальные обработчики (не тупики)
menus = tb.AutoConfigBot._main_menu_inline_keyboard(bot)
rows = menus.to_dict()["inline_keyboard"]
labels = [b["callback_data"] for row in rows for b in row if "callback_data" in b]
webapp_buttons = [b["web_app"]["url"] for row in rows for b in row if "web_app" in b]
expected = {"cmd_buy", "cmd_router", "cmd_dpi", "cmd_vps", "cmd_guide",
            "cmd_faq", "cmd_myorders", "cmd_support"}
assert set(labels) == expected, f"набор кнопок = {set(labels)}"
assert len(webapp_buttons) == 1 and "/webapp/" in webapp_buttons[0], \
    f"в меню должна быть кнопка Mini App: {webapp_buttons}"
for cb in sorted(expected):
    sent.clear()
    bot.last_cmd.clear()
    try:
        cb_handler(call(cb))
    except Exception as e:
        print(f"  ERROR {cb}: {type(e).__name__}: {e}")
        raise
    assert sent, f"кнопка {cb} должна показывать экран (не тупик)"
print(f"OK 6/6: все {len(expected)} кнопок меню открывают экраны")

# 7) Все статические callback_data бота не падают и не оставляют без ответа
bot.user_data[USER] = {
    "payment_id": tb.PaymentSystem().create_payment(USER, "menu_tester"),
    "server_ip": "95.217.1.1",
    "sni_hostname": "api.notion.com",
}
used = [b["callback_data"] for row in rows for b in row if "callback_data" in b]
known = {"cancel_purchase", "create_new", "install_self", "install_ssh",
         "make_dpi_payment", "make_payment", "pay_dpi_stars", "pay_stars",
         "retry_ssh_install", "update_existing"}
for cb in sorted(known):
    sent.clear()
    bot.last_cmd.clear()
    try:
        cb_handler(call(cb))
    except Exception as e:
        print(f"  ERROR {cb}: {type(e).__name__}: {e}")
        raise
    assert sent, f"callback {cb} должен на что-то ответить (не пустота)"
print(f"OK 7/7: все {len(known)} callback_data обрабатываются без падений")

# 8) Динамические колбэки router_* (все категории) и update_<order> (реальный заказ)
router_keys = list(tb.ROUTER_CATEGORIES.keys())
assert router_keys, "нет категорий роутеров"
for key in router_keys:
    sent.clear()
    bot.last_cmd.clear()
    cb_handler(call(f"router_{key}"))
    assert sent, f"router_{key} должен показывать подборку"
    assert any(s["markup"] for s in sent), f"router_{key} должен давать путь назад (кнопки)"
print(f"OK 8/9: все {len(router_keys)} категорий роутеров показывают подборку с меню")

sent.clear()
bot.last_cmd.clear()
pid = tb.PaymentSystem().create_payment(USER, "update_tester")
bot.user_data[USER] = {"payment_id": pid}
order_id = bot.payment_system.create_order(
    payment_id=pid, user_id=USER,
    config_path="", server_ip="95.217.1.1", uuid="u", sni_hostname="api.notion.com",
)
cb_handler(call(f"update_{order_id}"))
assert sent, "update_<order> должен отвечать"
print("OK 9/10: update_<order> с реальным заказом обрабатывается")

# 10) /status — хендлер состояния системы (упоминается в /guide и INSTRUCTIONS.md)
status_handler = None
for h in bot.bot.message_handlers:
    cmds = h.get("filters", {}).get("commands")
    if isinstance(cmds, list) and "status" in cmds:
        status_handler = h["function"]
        break
assert status_handler, "хендлер /status не найден"
sent.clear()
bot.last_cmd.clear()
status_handler(msg("/status"))
assert sent, "/status должен отвечать"
assert "Статус системы" in " ".join(s["text"] for s in sent)
assert any(s["markup"] for s in sent), "/status должен давать путь назад (меню)"
print("OK 10/10: /status -> состояние системы с меню")

# 11) Deep-link из Mini App: /start <payload> открывает нужный флоу
deep = None
for h in bot.bot.message_handlers:
    if h["function"].__name__ == "start_deep_link":
        deep = h["function"]
        break
assert deep, "хендлер deep-link (/start <payload>) не найден"

sent.clear()
bot.last_cmd.clear()
deep(msg("/start vps"))
text = " ".join(s["text"] for s in sent)
assert "1500" in text and "750" in text, "deep-link vps должен открывать покупку с ценами"
print("OK 11/13: /start vps -> покупка полного пакета")

sent.clear()
bot.last_cmd.clear()
deep(msg("/start dpi"))
text = " ".join(s["text"] for s in sent)
assert "1000" in text and "500" in text, "deep-link dpi должен открывать заказ обхода DPI"
print("OK 12/13: /start dpi -> заказ обхода DPI")

sent.clear()
bot.last_cmd.clear()
deep(msg("/start claude"))
text = " ".join(s["text"] for s in sent)
assert "по запросу" in text.lower() or "запросу" in text.lower(), "deep-link claude -> сообщение 'по запросу'"
assert any(s["markup"] for s in sent), "должна быть кнопка поддержки"
print("OK 13/13: /start claude -> запрос в поддержку")

# 14) /guide и /faq — команды из меню BotFather обязаны отвечать
for cmd, min_len, label in (("guide", 500, "инструкция"), ("faq", 500, "частые вопросы")):
    handler = None
    for h in bot.bot.message_handlers:
        cmds = h.get("filters", {}).get("commands")
        if isinstance(cmds, list) and cmd in cmds:
            handler = h["function"]
            break
    assert handler, f"хендлер /{cmd} не найден"
    sent.clear()
    bot.last_cmd.clear()
    handler(msg(f"/{cmd}"))
    assert sent, f"/{cmd} должен отвечать"
    body = " ".join(s["text"] for s in sent)
    assert len(body) >= min_len, f"/{cmd}: ответ слишком короткий ({len(body)} симв.) — ловит catch-all"
    assert any(s["markup"] for s in sent), f"/{cmd} должен давать главное меню"
    print(f"OK 14/14: /{cmd} -> {label} ({len(body)} симв. + меню)")

# 15) Пагинация /myorders: 7 заказов у изолированного юзера -> 2 части (5+2), меню на последней
pg_user, pg_chat = 999991, 1999991
up_user, up_chat = 999992, 1999992
ps = tb.PaymentSystem()
conn = ps._connect()
conn.execute("DELETE FROM orders WHERE user_id IN (?, ?)", (pg_user, up_user))
conn.execute("DELETE FROM payments WHERE user_id IN (?, ?)", (pg_user, up_user))
conn.commit()
conn.close()
for i in range(7):
    pid = ps.create_payment(pg_user, "pager", payment_id=f"pg_paginate_{i:02d}")
    ps.confirm_payment(pid)
    ps.create_order(payment_id=pid, user_id=pg_user, config_path="",
                    server_ip="95.217.1.1", uuid=f"page_{i:02d}", sni_hostname="api.notion.com")

orders_handler = None
for h in bot.bot.message_handlers:
    cmds = h.get("filters", {}).get("commands")
    if isinstance(cmds, list) and "myorders" in cmds:
        orders_handler = h["function"]
        break
assert orders_handler, "хендлер /myorders не найден"
sent.clear()
bot.last_cmd.clear()
orders_handler(msg("/myorders", user=pg_user, chat=pg_chat))
parts = [s for s in sent if "Ваши заказы" in s["text"]]
assert len(parts) == 2, f"/myorders при 7 заказах должен прислать 2 части, пришло {len(parts)}"
assert "(часть 1/2)" in parts[0]["text"] and "(часть 2/2)" in parts[1]["text"], \
    f"нет нумерации частей: {parts[0]['text'][:80]!r} | {parts[1]['text'][:80]!r}"
assert sent[-1]["markup"], "последняя часть должна нести главное меню"
assert all(s["parse_mode"] == "Markdown" for s in sent), "все части должны быть в Markdown"
print("OK 15/15: /myorders пагинация — 7 заказов в 2 части (5+2), меню на последней")

# 16) update_existing: >6 активных заказов -> 6 + «Показать ещё», затем остаток
ps2 = tb.PaymentSystem()
for i in range(7):
    pid = ps2.create_payment(up_user, "updater", payment_id=f"up_update_{i:02d}")
    ps2.confirm_payment(pid)
    ps2.create_order(payment_id=pid, user_id=up_user, config_path="",
                    server_ip="95.217.1.1", uuid=f"upd_{i:02d}", sni_hostname="api.notion.com")
bot.user_data[up_user] = {}

sent.clear()
bot.last_cmd.clear()
cb_handler(call("update_existing", user=up_user, chat=up_chat))
first_rows = sent[-1]["markup"].to_dict()["inline_keyboard"]
first_docs = [b["callback_data"] for row in first_rows for b in row if "callback_data" in b]
assert len([d for d in first_docs if d.startswith("update_order_")]) == 6, \
    f"первый экран должен показать 6 заказов: {first_docs}"
assert "update_more_6" in first_docs, "должна быть кнопка «Показать ещё»"

sent.clear()
bot.last_cmd.clear()
cb_handler(call("update_more_6", user=up_user, chat=up_chat))
second_rows = sent[-1]["markup"].to_dict()["inline_keyboard"]
second_docs = [b["callback_data"] for row in second_rows for b in row if "callback_data" in b]
assert len([d for d in second_docs if d.startswith("update_order_")]) == 1, \
    f"вторая страница — 1 оставшийся заказ: {second_docs}"
assert not any(d.startswith("update_more_") for d in second_docs), "запас исчерпан — «ещё» быть не должно"
print("OK 16/16: update_existing — 6 заказов + «Показать ещё» → 1 оставшийся без повтора")

# 17) Markdown-парity: Telegram отвечает 400 «can't parse entities», если
# entity (*, _, [, `) не закрыты. Так ломались /faq и /guide: 3 подчёркивания
# в @beliy_obhodchik_support_bot открывали незакрытый italic.
def md_balance(text):
    i, n = 0, len(text)
    bold = ital = 0
    code = False
    link_open = False
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if code:
            if c == "`":
                code = False
            i += 1
            continue
        if c == "`":
            code = True
        elif c == "*":
            bold ^= 1
        elif c == "_":
            ital ^= 1
        elif c == "[":
            if link_open:
                return f"вложенная [ в {i}"
            link_open = True
        elif c == "]":
            if not link_open:
                return f"лишняя ] в {i}"
            if not text.startswith("(", i + 1):
                return f"] без (url) в {i}"
            j = text.find(")", i + 1)
            if j < 0:
                return f"незакрытый (url) в {i}"
            link_open = False
            i = j + 1
            continue
        i += 1
    if code:
        return "незакрытая `"
    if bold:
        return "незакрытая *"
    if ital:
        return "незакрытая _"
    if link_open:
        return "незакрытая ["
    return None

bad = [(t, md_balance(t)) for t in md_texts if md_balance(t)]
assert not bad, f"битый Markdown: {bad[0][1]} :: {bad[0][0][:120]!r}"
for t in tb.AutoConfigBot.SUPPORT_ANSWERS.values():
    r = md_balance(t)
    assert r is None, f"SUPPORT_ANSWERS: {r} :: {t[:80]!r}"
print(f"OK 17/17: markdown-parity {len(md_texts)} сообщений + SUPPORT_ANSWERS — entity закрыты")

print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")